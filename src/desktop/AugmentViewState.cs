using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace hexgod
{
    // A missing title is not evidence that the player closed the panel.
    // This state is separate from the main window's historical OCR cards.
    sealed class AugmentViewState
    {
        public bool Visible { get; private set; }
        public Dictionary<string, object> Result { get; private set; }
        public long SelectionAt { get; private set; }
        long lastEvidence, lastTitles, firstMiss;
        int misses;
        long window;
        long lastCaptured;
        string[] closedNames;
        static string[] Names(Dictionary<string, object> result) {
            return Json.A(result ?? Json.D(), "choices").Select(Json.Obj)
                .Select(c => Json.B(c, "matched") ? Json.S(c, "name") : "").ToArray();
        }
        public void Reset() {
            Visible = false; Result = null; SelectionAt = 0; lastEvidence = lastTitles = firstMiss = 0;
            misses = 0; window = 0; lastCaptured = 0; closedNames = null;
        }
        public void Selected(long now) {
            if (Result != null) closedNames = Names(Result);
            SelectionAt = now;
            Visible = false; Result = null; misses = 0;
        }
        public bool Observe(Dictionary<string, object> result, long now) {
            long captured = (long)Json.N(result, "capturedAtMs");
            if (SelectionAt > 0 && captured <= SelectionAt) return false;
            if (captured > 0 && captured <= lastCaptured) return false;
            if (captured > 0) lastCaptured = captured;
            if (Json.B(result, "captureSkipped")) {
                if (now - lastEvidence > 2500) { Visible = false; Result = null; }
                return false;
            }
            long seenWindow = (long)Json.N(result, "gameWindowHwnd");
            if (seenWindow > 0 && window > 0 && seenWindow != window) Reset();
            if (seenWindow > 0) window = seenWindow;
            bool panel = Json.B(result, "augmentPanelVisible");
            if (!panel) {
                if (++misses == 1) firstMiss = now;
                if (misses >= 2 && now - firstMiss >= 400) {
                    Visible = false; Result = null; closedNames = null;
                }
                return false;
            }
            misses = 0; lastEvidence = now;
            var names = Names(result);
            if (closedNames != null) {
                bool changed = names.Select((name, i) => name.Length > 0 && i < closedNames.Length &&
                    closedNames[i].Length > 0 && name != closedNames[i]).Any(value => value);
                if (!changed) return false;
                closedNames = null;
            }
            Visible = true;
            var fingerprints = Json.A(result, "titleFingerprints").Select(Convert.ToString).ToArray();
            var previousFingerprints = Json.A(Json.Obj(Result), "titleFingerprints").Select(Convert.ToString).ToArray();
            if (fingerprints.Length == 3 && previousFingerprints.Length == 3 &&
                fingerprints.Where((value, i) => !String.IsNullOrEmpty(value) &&
                    !String.IsNullOrEmpty(previousFingerprints[i]) && value != previousFingerprints[i]).Any()) Result = null;
            if (Json.N(result, "matchedCount") > 0) { Result = result; lastTitles = now; return true; }
            if (now - lastTitles > 1200) Result = null;
            return false;
        }
    }


}
