// Single source of truth for the FO4_Wrld wire-protocol version.
//
// Three places must agree or peers silently desync:
//   - fw_native/src/net/protocol.h   (the C++ client, static_asserts against this)
//   - net/protocol.py                (the Python server)
//   - fw_launcher                    (advertises the version in Steam lobby data
//                                     so a mismatched joiner is rejected up front
//                                     instead of after FO4 has already launched)
//
// This header is deliberately macro-only and includes nothing, so the
// launcher can pull it in without dragging in the whole protocol surface.
//
// Bump this AND net/protocol.py's PROTOCOL_VERSION together, in the same
// commit, whenever the wire format changes.

#pragma once

#define FW_PROTOCOL_VERSION 25
