// The background half of FoM.
//
// WHY THIS EXISTS
//
// Steam only delivers "your friend invited you" / "Join Game" to a process
// that is *already running* as the app in question. If nothing is running,
// Steam launches whatever executable is registered for that AppID - and for
// AppID 480 that is Valve's Spacewar, not us. We cannot change that.
//
// So instead of asking the player to start FoM before their friend invites
// them (which is not co-op, that is homework), FoM keeps a tiny resident
// agent alive. It sits in Steam's callback queue doing nothing measurable
// until an invite arrives, then runs the entire join by itself: enter the
// lobby, stand up the tunnel, write fw_config.ini, launch Fallout 4.
//
// From the player's side that is exactly native co-op: click Accept in
// Steam, the game opens, you are in your friend's world.
//
// SINGLE INSTANCE
//
// Two processes both claiming AppID 480 on one machine is a coin flip over
// which one Steam hands the invite to. A named mutex makes the agent the
// only Steam session, and a named pipe lets a freshly double-clicked
// FoM.exe hand its command to the agent instead of racing it.

#pragma once

#include <cstdint>
#include <string>

namespace fom {

// ---------------------------------------------------------------- instance

// True if this process won the race and is now THE FoM instance. Held for
// process lifetime; there is no unlock, the handle dies with us.
bool claim_single_instance();

// Hand the crown over. Call immediately before spawning the agent and
// exiting, so the new agent can claim it without a retry loop.
void release_single_instance();

// Is an agent already resident? (Cheap: probes the pipe.)
bool agent_is_running();

// Commands the resident agent understands.
enum class AgentCommand {
    ShowMenu,   // player double-clicked FoM.exe
    Host,
    Join,
    Lan,
    Quit,
};

enum class AgentReply {
    NoAgent,    // nothing resident (or it did not answer in time)
    Accepted,
    Busy,       // resident, but mid-session / showing a window
};

// Send `cmd` to the resident agent. Never blocks for more than a couple of
// seconds, even if the agent is wedged - a launcher that can hang on its own
// background helper is worse than no helper.
AgentReply send_to_agent(AgentCommand cmd, std::uint64_t lobby = 0);

// Server side of the pipe.
//
// This runs on its own thread rather than being polled, because the agent
// spends most of a session blocked - on a menu prompt, or inside the tunnel
// loop - and a resident helper that stops answering the moment it is doing
// something is not much of a service. Commands are queued for the agent's
// main loop; the only one handled on the pipe thread is the "are you busy"
// decision, which needs to be answered immediately.
class AgentServer {
public:
    AgentServer();
    ~AgentServer();

    AgentServer(const AgentServer&) = delete;
    AgentServer& operator=(const AgentServer&) = delete;

    bool listening() const;

    // The agent declares whether it can take new work. While busy, incoming
    // commands are refused with AgentReply::Busy instead of being queued
    // behind a session that may last hours.
    void set_busy(bool busy);

    // Pop one queued command. Never blocks.
    bool next(AgentCommand* cmd_out, std::uint64_t* lobby_out);

    // Someone asked us to shut down while we were idle.
    bool quit_requested() const;

    // Opaque; defined in agent.cpp. Public only so the listener thread
    // function can name it.
    struct Impl;

private:
    Impl* impl_ = nullptr;
};

// ---------------------------------------------------------------- autostart

// HKCU\...\Run entry so the agent is there when the invite arrives. This is
// the difference between "co-op" and "co-op, but text your friend first".
bool autostart_enabled();
bool enable_autostart();    // idempotent
bool disable_autostart();

// ------------------------------------------------------------ remembered FO4

// The agent has no one to ask, so the interactive run leaves it a note.
void        remember_fo4_dir(const std::wstring& dir);
std::wstring recall_fo4_dir();

// --------------------------------------------------------------- console

// The agent runs windowless until it has something to say.
void hide_console();
void show_console();

// Nudge the console window to the foreground when a session starts, so the
// player sees what happened after clicking Accept.
void raise_console();

}  // namespace fom
