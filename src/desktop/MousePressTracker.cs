namespace hexgod
{
    // Shared by the two read-only input sources. Track our own high-bit edges;
    // GetAsyncKeyState's unreliable "pressed since last call" bit is never used.
    sealed class MousePressTracker
    {
        bool sampled, down;
        long lastAt = -1000;
        string lastSource;

        public bool ObserveState(bool pressed) {
            if (!sampled) { sampled = true; down = pressed; return false; }
            bool edge = pressed && !down;
            down = pressed;
            return edge;
        }

        public bool Accept(string source, long at) {
            bool duplicate = lastSource != null && source != lastSource && at >= lastAt && at - lastAt <= 80;
            lastSource = source;
            if (duplicate) return false;
            lastAt = at;
            return true;
        }
    }
}
