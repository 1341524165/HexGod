using System;
using System.Collections.Generic;

namespace hexgod
{
    // Pure transitions: a transient API failure is not a match ending.
    sealed class GameSessionState
    {
        public bool Observed { get; private set; }
        public bool Active { get; private set; }
        public bool CanCapture { get; private set; }
        public bool Supported { get; private set; }
        public string Phase { get; private set; }
        public int Generation { get; private set; }
        string gameId = "";
        double lastGameTime;
        long lastEvidence;

        public bool Update(Dictionary<string, object> live, long now) {
            Observed = true;
            var lcu = Json.Obj(Json.Get(live, "lcu"));
            bool connected = Json.B(lcu, "available");
            string phase = connected ? Json.S(lcu, "phase") : "";
            bool playing = phase == "InProgress" || phase == "Reconnect" || phase == "GameStart";
            bool available = Json.B(live, "available");
            // A running game's Live API is fresher than the launcher's queue.
            string mode = available ? Json.S(live, "gameMode") : "";
            if (mode.Length == 0) mode = Json.S(lcu, "gameMode");
            Supported = mode.Length > 0 ? IsSupportedMode(mode) : !Active || Supported;
            bool windowPresent = Json.B(live, "gameWindowPresent");
            bool ended = phase == "WaitingForStats" || phase == "PreEndOfGame" || phase == "EndOfGame";
            string nextId = Json.S(lcu, "gameId");
            bool reset = false;
            if (connected && !playing && (ended || (!windowPresent && !available))) {
                reset = Active;
                Active = CanCapture = false;
                gameId = ""; lastGameTime = 0; Phase = phase;
            } else if (available || playing || windowPresent) {
                bool newId = gameId.Length > 0 && nextId.Length > 0 && nextId != gameId;
                bool clockRestart = available && lastGameTime > Json.N(live, "gameTime") + 30;
                reset = !Active || newId || clockRestart;
                if (reset) { lastGameTime = 0; gameId = ""; if (mode.Length == 0) Supported = true; }
                Active = true;
                if (available) lastGameTime = Json.N(live, "gameTime");
                if (nextId.Length > 0) gameId = nextId;
                lastEvidence = now;
                CanCapture = Supported && (windowPresent || available || phase == "InProgress" || phase == "Reconnect");
                Phase = connected && playing ? phase : "LiveFallback";
            } else {
                CanCapture = false;
                if (Active && now - lastEvidence >= 10000) {
                    Active = false; reset = true; gameId = ""; lastGameTime = 0;
                }
                Phase = Active ? "Reconnecting" : "Disconnected";
            }
            if (reset) Generation++;
            return reset;
        }

        internal static bool IsSupportedMode(string mode) {
            // aramgg_client identifies ARAM: Mayhem as KIWI, not ARAM.
            return mode.Equals("KIWI", StringComparison.OrdinalIgnoreCase) ||
                mode.StartsWith("ARAM", StringComparison.OrdinalIgnoreCase);
        }
    }
}
