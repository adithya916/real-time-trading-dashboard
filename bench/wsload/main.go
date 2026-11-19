// wsload: how many dashboard viewers can one gateway serve, and how fresh is what they get?
//
//	go run . -url ws://localhost:8080/ws/sim/BTC-SIM -steps 1000,2000,5000,10000 -hold 60s
//
// An open-model load test: viewers connect at a constant arrival rate (-rate per second),
// whether or not earlier connections have finished, until each step's count is reached; the
// step is then held. Every viewer
//
//   - reads every message (one per gateway tick, several envelopes in it) and, for the
//     envelopes the gateway built (snapshots and deltas), records
//     staleness = now - ts_gw_send_ns: how old the book update is when the viewer has it;
//   - records bus-to-viewer = now - ts_bus_ns for trades, which the gateway forwards as is;
//   - pings every -ping interval and records the round trip with HdrHistogram's
//     coordinated-omission correction, so a stalled gateway shows up at its real weight.
//
// The clock comparisons assume the load generator runs on the gateway's machine (same clock).
// Results: one row per step in -csv, and a markdown table on stdout.
//
// Before 10,000 viewers on Linux, raise the limits:
//
//	ulimit -n 65536; sudo sysctl -w net.ipv4.ip_local_port_range="1024 65535"
package main

import (
	"context"
	"encoding/csv"
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/HdrHistogram/hdrhistogram-go"
	"github.com/gorilla/websocket"
)

const maxMicros = 60_000_000 // histograms cover up to 60 s, 3 significant digits

type stats struct {
	mu        sync.Mutex
	rtt       *hdrhistogram.Histogram // µs
	staleness *hdrhistogram.Histogram // µs
	busToView *hdrhistogram.Histogram // µs
	messages  atomic.Int64
	frames    atomic.Int64
	bytes     atomic.Int64
	errors    atomic.Int64
	closed    atomic.Int64
	connected atomic.Int64
}

func newStats() *stats {
	return &stats{
		rtt:       hdrhistogram.New(1, maxMicros, 3),
		staleness: hdrhistogram.New(1, maxMicros, 3),
		busToView: hdrhistogram.New(1, maxMicros, 3),
	}
}

// reset starts a new measurement window, keeping the connection count.
func (s *stats) reset() {
	s.mu.Lock()
	s.rtt.Reset()
	s.staleness.Reset()
	s.busToView.Reset()
	s.mu.Unlock()
	s.messages.Store(0)
	s.frames.Store(0)
	s.bytes.Store(0)
	s.errors.Store(0)
	s.closed.Store(0)
}

func clampMicros(ns int64) int64 {
	us := ns / 1000
	if us < 1 {
		return 1
	}
	if us > maxMicros {
		return maxMicros
	}
	return us
}

// message handles one binary WebSocket message: one gateway tick, several envelopes.
func (s *stats) message(b []byte, now int64) {
	s.messages.Add(1)
	s.bytes.Add(int64(len(b)))
	frames, err := splitBatch(b)
	if err != nil {
		s.errors.Add(1)
		return
	}
	for _, f := range frames {
		s.frame(f, now)
	}
}

func (s *stats) frame(b []byte, now int64) {
	s.frames.Add(1)
	st, err := parseStamps(b)
	if err != nil {
		s.errors.Add(1)
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case st.TsGwSend > 0 && (st.Kind == "delta" || st.Kind == "snapshot"):
		_ = s.staleness.RecordValue(clampMicros(now - st.TsGwSend))
	case st.Kind == "trade" && st.TsBus > 0:
		_ = s.busToView.RecordValue(clampMicros(now - st.TsBus))
	}
}

func (s *stats) roundTrip(ns int64, interval time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	_ = s.rtt.RecordCorrectedValue(clampMicros(ns), interval.Microseconds())
}

func viewer(ctx context.Context, url string, ping time.Duration, s *stats, wg *sync.WaitGroup) {
	defer wg.Done()
	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
	conn, _, err := dialer.DialContext(ctx, url, nil)
	if err != nil {
		s.errors.Add(1)
		return
	}
	s.connected.Add(1)
	defer func() {
		s.connected.Add(-1)
		conn.Close()
	}()
	go func() {
		ticker := time.NewTicker(ping)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				_ = conn.WriteControl(websocket.CloseMessage, nil, time.Now().Add(time.Second))
				conn.Close()
				return
			case <-ticker.C:
				msg := "ping:" + strconv.FormatInt(time.Now().UnixNano(), 10)
				if conn.WriteMessage(websocket.TextMessage, []byte(msg)) != nil {
					return
				}
			}
		}
	}()
	for {
		typ, data, err := conn.ReadMessage()
		if err != nil {
			if ctx.Err() == nil {
				s.closed.Add(1) // the gateway closed us, e.g. 1013 for falling behind
			}
			return
		}
		now := time.Now().UnixNano()
		if typ == websocket.TextMessage {
			if sent, err := strconv.ParseInt(strings.TrimPrefix(string(data), "ping:"), 10, 64); err == nil {
				s.roundTrip(now-sent, ping)
			}
			continue
		}
		s.message(data, now)
	}
}

type result struct {
	Viewers, Connected                     int64
	Errors, Closed                         int64
	MessagesPerViewerPerS                  float64
	FramesPerViewerPerS, KiBPerViewerPerS  float64
	RTT, Staleness, BusToView              [4]float64 // p50, p99, p99.9, max in ms
	RTTSamples, StaleSamples, TradeSamples int64
}

func percentiles(h *hdrhistogram.Histogram) [4]float64 {
	return [4]float64{
		float64(h.ValueAtQuantile(50)) / 1000,
		float64(h.ValueAtQuantile(99)) / 1000,
		float64(h.ValueAtQuantile(99.9)) / 1000,
		float64(h.Max()) / 1000,
	}
}

func parseSteps(spec string) ([]int, error) {
	var steps []int
	for _, part := range strings.Split(spec, ",") {
		n, err := strconv.Atoi(strings.TrimSpace(part))
		if err != nil || n <= 0 {
			return nil, fmt.Errorf("bad step %q", part)
		}
		if len(steps) > 0 && n <= steps[len(steps)-1] {
			return nil, fmt.Errorf("steps must increase: %v then %d", steps, n)
		}
		steps = append(steps, n)
	}
	return steps, nil
}

func main() {
	url := flag.String("url", "ws://localhost:8080/ws/sim/BTC-SIM", "gateway stream URL")
	stepsFlag := flag.String("steps", "100,500,1000", "viewer counts to hold, increasing")
	rate := flag.Float64("rate", 200, "new connections per second while ramping")
	hold := flag.Duration("hold", 30*time.Second, "how long each step is measured")
	settle := flag.Duration("settle", 5*time.Second, "wait after reaching a step before measuring")
	ping := flag.Duration("ping", time.Second, "ping interval per viewer")
	csvPath := flag.String("csv", "", "write one row per step here")
	flag.Parse()

	steps, err := parseSteps(*stepsFlag)
	if err != nil {
		log.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s := newStats()
	var wg sync.WaitGroup
	interval := time.Duration(float64(time.Second) / *rate)
	opened := 0
	var results []result
	for _, target := range steps {
		ticker := time.NewTicker(interval)
		for opened < target {
			<-ticker.C
			wg.Add(1)
			go viewer(ctx, *url, *ping, s, &wg)
			opened++
		}
		ticker.Stop()
		time.Sleep(*settle)
		s.reset()
		start := time.Now()
		time.Sleep(*hold)
		elapsed := time.Since(start).Seconds()
		s.mu.Lock()
		connected := s.connected.Load()
		per := float64(max(connected, 1)) * elapsed
		r := result{
			Viewers:             int64(target),
			Connected:           connected,
			Errors:              s.errors.Load(),
			Closed:              s.closed.Load(),
			MessagesPerViewerPerS: float64(s.messages.Load()) / per,
			FramesPerViewerPerS:   float64(s.frames.Load()) / per,
			KiBPerViewerPerS:    float64(s.bytes.Load()) / 1024 / per,
			RTT:                 percentiles(s.rtt),
			Staleness:           percentiles(s.staleness),
			BusToView:           percentiles(s.busToView),
			RTTSamples:          s.rtt.TotalCount(),
			StaleSamples:        s.staleness.TotalCount(),
			TradeSamples:        s.busToView.TotalCount(),
		}
		s.mu.Unlock()
		results = append(results, r)
		log.Printf("step %d: %d connected, %d errors, %d closed, rtt p99 %.1f ms, staleness p99 %.1f ms",
			target, r.Connected, r.Errors, r.Closed, r.RTT[1], r.Staleness[1])
	}
	cancel()
	wg.Wait()
	printTable(results)
	if *csvPath != "" {
		if err := writeCSV(*csvPath, results); err != nil {
			log.Fatal(err)
		}
	}
}

func printTable(results []result) {
	fmt.Println("| Viewers | Connected | Errors | Closed | Messages/s per viewer | Envelopes/s per viewer | KiB/s per viewer | RTT p50 / p99 / p99.9 / max (ms) | Staleness p50 / p99 / p99.9 / max (ms) | Bus to viewer p50 / p99 (ms) |")
	fmt.Println("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |")
	q := func(v [4]float64) string { return fmt.Sprintf("%.1f / %.1f / %.1f / %.1f", v[0], v[1], v[2], v[3]) }
	for _, r := range results {
		fmt.Printf("| %d | %d | %d | %d | %.1f | %.1f | %.2f | %s | %s | %.1f / %.1f |\n",
			r.Viewers, r.Connected, r.Errors, r.Closed, r.MessagesPerViewerPerS, r.FramesPerViewerPerS, r.KiBPerViewerPerS,
			q(r.RTT), q(r.Staleness), r.BusToView[0], r.BusToView[1])
	}
}

func writeCSV(path string, results []result) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	header := []string{"viewers", "connected", "errors", "closed", "messages_per_viewer_s", "frames_per_viewer_s", "kib_per_viewer_s"}
	for _, name := range []string{"rtt", "staleness", "bus_to_viewer"} {
		header = append(header, name+"_p50_ms", name+"_p99_ms", name+"_p999_ms", name+"_max_ms")
	}
	header = append(header, "rtt_samples", "staleness_samples", "trade_samples")
	if err := w.Write(header); err != nil {
		return err
	}
	f2 := func(v float64) string { return strconv.FormatFloat(v, 'f', 3, 64) }
	for _, r := range results {
		row := []string{
			strconv.FormatInt(r.Viewers, 10), strconv.FormatInt(r.Connected, 10),
			strconv.FormatInt(r.Errors, 10), strconv.FormatInt(r.Closed, 10),
			f2(r.MessagesPerViewerPerS), f2(r.FramesPerViewerPerS), f2(r.KiBPerViewerPerS),
		}
		for _, q := range [][4]float64{r.RTT, r.Staleness, r.BusToView} {
			row = append(row, f2(q[0]), f2(q[1]), f2(q[2]), f2(q[3]))
		}
		row = append(row, strconv.FormatInt(r.RTTSamples, 10), strconv.FormatInt(r.StaleSamples, 10),
			strconv.FormatInt(r.TradeSamples, 10))
		if err := w.Write(row); err != nil {
			return err
		}
	}
	w.Flush()
	return w.Error()
}
