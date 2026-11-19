package main

import (
	"testing"
	"time"

	"google.golang.org/protobuf/encoding/protowire"
)

// envelope builds the wire bytes the gateway would send, field by field.
func envelope(seq uint64, tsBus, tsGwSend int64, payloadField protowire.Number) []byte {
	var b []byte
	b = protowire.AppendTag(b, 1, protowire.BytesType)
	b = protowire.AppendString(b, "sim")
	b = protowire.AppendTag(b, fieldSeq, protowire.VarintType)
	b = protowire.AppendVarint(b, seq)
	b = protowire.AppendTag(b, fieldTsBus, protowire.VarintType)
	b = protowire.AppendVarint(b, uint64(tsBus))
	if tsGwSend > 0 {
		b = protowire.AppendTag(b, fieldTsGwSend, protowire.VarintType)
		b = protowire.AppendVarint(b, uint64(tsGwSend))
	}
	// A nested message with one varint field, standing in for the payload.
	inner := protowire.AppendTag(nil, 1, protowire.VarintType)
	inner = protowire.AppendVarint(inner, 42)
	b = protowire.AppendTag(b, payloadField, protowire.BytesType)
	b = protowire.AppendBytes(b, inner)
	return b
}

func TestParseStampsReadsKindAndTimestamps(t *testing.T) {
	s, err := parseStamps(envelope(7, 1_000, 2_000, fieldDelta))
	if err != nil {
		t.Fatal(err)
	}
	if s.Seq != 7 || s.TsBus != 1_000 || s.TsGwSend != 2_000 || s.Kind != "delta" {
		t.Fatalf("got %+v", s)
	}
	trade, _ := parseStamps(envelope(8, 5, 0, fieldTrade))
	if trade.Kind != "trade" || trade.TsGwSend != 0 {
		t.Fatalf("got %+v", trade)
	}
	strategy, _ := parseStamps(envelope(1, 5, 0, fieldStrategy))
	if strategy.Kind != "strategy" {
		t.Fatalf("got %+v", strategy)
	}
}

func TestParseStampsRejectsTruncatedFrames(t *testing.T) {
	b := envelope(7, 1_000, 2_000, fieldDelta)
	if _, err := parseStamps(b[:len(b)-1]); err == nil {
		t.Fatal("a truncated frame should not parse")
	}
}

func TestFramesFeedTheRightHistograms(t *testing.T) {
	s := newStats()
	now := int64(10_000_000)
	s.frame(envelope(1, 0, now-3_000_000, fieldDelta), now) // 3 ms stale
	s.frame(envelope(2, now-5_000_000, 0, fieldTrade), now) // 5 ms bus to viewer
	s.frame(envelope(3, 0, 0, fieldAnalytics), now)          // counted, not timed
	if s.staleness.TotalCount() != 1 || s.busToView.TotalCount() != 1 || s.frames.Load() != 3 {
		t.Fatalf("staleness %d, trades %d, frames %d", s.staleness.TotalCount(), s.busToView.TotalCount(), s.frames.Load())
	}
	if got := s.staleness.Max(); got < 2990 || got > 3010 {
		t.Fatalf("staleness %d µs, want about 3000", got)
	}
}

func TestRoundTripsAreCorrectedForCoordinatedOmission(t *testing.T) {
	s := newStats()
	s.roundTrip(int64(5*time.Second), time.Second) // one 5 s stall while pinging every second
	if s.rtt.TotalCount() != 5 {
		t.Fatalf("got %d samples, want the stall plus the 4 pings it blocked", s.rtt.TotalCount())
	}
}

func TestParseSteps(t *testing.T) {
	steps, err := parseSteps("100, 500,1000")
	if err != nil || len(steps) != 3 || steps[2] != 1000 {
		t.Fatalf("got %v, %v", steps, err)
	}
	for _, bad := range []string{"", "10,5", "a", "0"} {
		if _, err := parseSteps(bad); err == nil {
			t.Fatalf("%q should be rejected", bad)
		}
	}
}
