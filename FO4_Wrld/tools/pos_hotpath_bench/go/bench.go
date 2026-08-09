// POS hot-path microbench — same algorithm as python/bench.py
package main

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"time"
)

const (
	maxSpeed    = 2500.0
	maxVSpeed   = 5000.0
	minDtMs     = 20.0
	coordBound  = 1e7
	peerIDLen   = 16
	bodyLen     = 36
	ok          = 0
	tooFast     = 5
	speedCode   = 2
	vertical    = 4
	nonFinite   = 10
	tsInv       = 3
	nPeers      = 4
)

type peer struct {
	peerID    [peerIDLen]byte
	lastX     float32
	lastY     float32
	lastZ     float32
	lastTs    uint64
	lastCell  uint32
	lastAtMs  float64
	has       bool
}

func packPos(buf []byte, x, y, z, rx, ry, rz float32, ts uint64, cell uint32) {
	putF32(buf[0:], x)
	putF32(buf[4:], y)
	putF32(buf[8:], z)
	putF32(buf[12:], rx)
	putF32(buf[16:], ry)
	putF32(buf[20:], rz)
	putU64(buf[24:], ts)
	putU32(buf[32:], cell)
}

func unpackPos(buf []byte) (x, y, z, rx, ry, rz float32, ts uint64, cell uint32) {
	return getF32(buf[0:]), getF32(buf[4:]), getF32(buf[8:]),
		getF32(buf[12:]), getF32(buf[16:]), getF32(buf[20:]),
		getU64(buf[24:]), getU32(buf[32:])
}

func putF32(b []byte, v float32) {
	u := math.Float32bits(v)
	b[0] = byte(u)
	b[1] = byte(u >> 8)
	b[2] = byte(u >> 16)
	b[3] = byte(u >> 24)
}
func getF32(b []byte) float32 {
	u := uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
	return math.Float32frombits(u)
}
func putU64(b []byte, v uint64) {
	for i := 0; i < 8; i++ {
		b[i] = byte(v >> (8 * i))
	}
}
func getU64(b []byte) uint64 {
	var v uint64
	for i := 0; i < 8; i++ {
		v |= uint64(b[i]) << (8 * i)
	}
	return v
}
func putU32(b []byte, v uint32) {
	b[0] = byte(v)
	b[1] = byte(v >> 8)
	b[2] = byte(v >> 16)
	b[3] = byte(v >> 24)
}
func getU32(b []byte) uint32 {
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
}

func validate(p *peer, x, y, z, rx, ry, rz float32, ts uint64, cell uint32, nowMs float64) uint8 {
	if math.IsNaN(float64(x)) || math.IsInf(float64(x), 0) ||
		math.IsNaN(float64(y)) || math.IsInf(float64(y), 0) ||
		math.IsNaN(float64(z)) || math.IsInf(float64(z), 0) ||
		math.IsNaN(float64(rx)) || math.IsInf(float64(rx), 0) ||
		math.IsNaN(float64(ry)) || math.IsInf(float64(ry), 0) ||
		math.IsNaN(float64(rz)) || math.IsInf(float64(rz), 0) {
		return nonFinite
	}
	if math.Abs(float64(x)) > coordBound || math.Abs(float64(y)) > coordBound || math.Abs(float64(z)) > coordBound {
		return nonFinite
	}
	if !p.has {
		return ok
	}
	dtMs := nowMs - p.lastAtMs
	if dtMs < minDtMs {
		return tooFast
	}
	if ts < p.lastTs {
		return tsInv
	}
	if cell != 0 && p.lastCell != 0 && cell != p.lastCell {
		return ok
	}
	dtS := dtMs / 1000.0
	dx := float64(x - p.lastX)
	dy := float64(y - p.lastY)
	dz := float64(z - p.lastZ)
	dist := math.Sqrt(dx*dx + dy*dy + dz*dz)
	if dist/dtS > maxSpeed {
		return speedCode
	}
	if math.Abs(dz)/dtS > maxVSpeed {
		return vertical
	}
	return ok
}

func run(iters int) {
	peers := make([]peer, nPeers)
	bodies := make([][]byte, nPeers)
	for i := 0; i < nPeers; i++ {
		copy(peers[i].peerID[:], fmt.Sprintf("player_%d", i))
		bodies[i] = make([]byte, bodyLen)
		packPos(bodies[i], -76000+float32(i)*100, 93000, 7700, 0, 0, 0.1, 1000000+uint64(i), 0x00024A02)
	}
	out := make([]byte, (peerIDLen+bodyLen)*(nPeers-1))
	var accepts, rejects uint64
	now := 1000000.0
	t0 := time.Now()
	for k := 0; k < iters; k++ {
		for i := 0; i < nPeers; i++ {
			x, y, z, rx, ry, rz, ts, cell := unpackPos(bodies[i])
			x += 6
			ts += 20
			packPos(bodies[i], x, y, z, rx, ry, rz, ts, cell)
			now += 20
			reason := validate(&peers[i], x, y, z, rx, ry, rz, ts, cell, now)
			if reason != ok {
				rejects++
				continue
			}
			accepts++
			peers[i].lastX, peers[i].lastY, peers[i].lastZ = x, y, z
			peers[i].lastTs = ts
			peers[i].lastCell = cell
			peers[i].lastAtMs = now
			peers[i].has = true
			o := 0
			for j := 0; j < nPeers; j++ {
				if j == i {
					continue
				}
				copy(out[o:], peers[i].peerID[:])
				copy(out[o+peerIDLen:], bodies[i])
				o += peerIDLen + bodyLen
			}
		}
	}
	elapsed := time.Since(t0).Seconds()
	ops := float64(iters * nPeers)
	fmt.Printf("lang=go peers=%d iters=%d ops=%.0f\n", nPeers, iters, ops)
	fmt.Printf("elapsed_s=%.6f ns_per_op=%.1f ops_per_s=%.0f\n", elapsed, elapsed*1e9/ops, ops/elapsed)
	fmt.Printf("accepts=%d rejects=%d\n", accepts, rejects)
}

func main() {
	iters := 500000
	if len(os.Args) > 1 {
		if v, err := strconv.Atoi(os.Args[1]); err == nil {
			iters = v
		}
	}
	run(iters)
}
