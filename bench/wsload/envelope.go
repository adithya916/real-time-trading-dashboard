package main

import (
	"errors"

	"google.golang.org/protobuf/encoding/protowire"
)

// Field numbers in marketdata.v1.Envelope (proto/marketdata/v1/marketdata.proto).
const (
	fieldSeq       = 3
	fieldTsExch    = 4
	fieldTsRecv    = 5
	fieldTsBus     = 6
	fieldTsGwSend  = 7
	fieldSnapshot  = 10
	fieldDelta     = 11
	fieldTrade     = 12
	fieldReset     = 13
	fieldAnalytics = 14
	fieldStrategy  = 15
)

// Stamps is what the load generator needs from a frame: its kind and the hop timestamps.
// Reading these few fields straight off the wire avoids generating Go code for the schema.
type Stamps struct {
	Seq      uint64
	TsExch   int64
	TsRecv   int64
	TsBus    int64
	TsGwSend int64
	Kind     string
}

var errMalformed = errors.New("malformed envelope")

func parseStamps(b []byte) (Stamps, error) {
	var s Stamps
	for len(b) > 0 {
		num, typ, n := protowire.ConsumeTag(b)
		if n < 0 {
			return s, errMalformed
		}
		b = b[n:]
		if typ == protowire.VarintType {
			v, m := protowire.ConsumeVarint(b)
			if m < 0 {
				return s, errMalformed
			}
			b = b[m:]
			switch num {
			case fieldSeq:
				s.Seq = v
			case fieldTsExch:
				s.TsExch = int64(v)
			case fieldTsRecv:
				s.TsRecv = int64(v)
			case fieldTsBus:
				s.TsBus = int64(v)
			case fieldTsGwSend:
				s.TsGwSend = int64(v)
			}
			continue
		}
		switch num {
		case fieldSnapshot:
			s.Kind = "snapshot"
		case fieldDelta:
			s.Kind = "delta"
		case fieldTrade:
			s.Kind = "trade"
		case fieldReset:
			s.Kind = "reset"
		case fieldAnalytics:
			s.Kind = "analytics"
		case fieldStrategy:
			s.Kind = "strategy"
		}
		m := protowire.ConsumeFieldValue(num, typ, b)
		if m < 0 {
			return s, errMalformed
		}
		b = b[m:]
	}
	return s, nil
}
