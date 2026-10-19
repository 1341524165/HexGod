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
    sealed partial class Controller
    {
        static string Samples(Dictionary<string, object> row) {
            return Json.Get(row, "games") == null ? "来源未公布样本量" : Json.Count(Json.N(row, "games")) + " 场";
        }
        void RenderItems(Dictionary<string, object> detail) {
            jointDataStatus = Json.S(detail, "jointDataStatus");
            jointContextAvailable = Json.B(detail, "jointContextAvailable");
            var profile = Json.Obj(Json.Get(detail, "buildProfile"));
            string core = String.Join("  ·  ", Json.A(detail, "coreItems").Select(x => Json.S(Json.Obj(x), "name")));
            var starterPlan = Json.Obj(Json.Get(detail, "starterPlan"));
            string starters = String.Join(" + ", Json.A(starterPlan, "items").Select(x => ItemQuantity(Json.Obj(x))));
            string path = "出门 1400 金：" + starters + "（合计 " + Json.N(starterPlan, "totalCost") +
                " 金，余 " + Json.N(starterPlan, "remainingGold") + " 金）\n" +
                Json.S(starterPlan, "reason") + "\n" + Json.S(detail, "sourceNote") + "\n" +
                (core.Length == 0 ? "暂无精选核心方案，可参考下方装备统计。" : "核心参考：" + core);
            if (starters.Length == 0) path = Json.S(starterPlan, "reason") + "\n" + Json.S(detail, "sourceNote") + "\n核心参考：" + core;
            string mechanismItems = String.Join("、", Json.A(detail, "mechanismItems").Select(Convert.ToString));
            string requiredItems = String.Join("、", Json.A(detail, "requiredItems").Select(Convert.ToString));
            string buildText = Json.S(profile, "basis") == "mechanism_rule" ?
                "当前方向：" + Json.S(profile, "name") + "（机制规则）\n" + Json.S(profile, "reason") +
                (requiredItems.Length > 0 ? "\n任务或升级指定：" + requiredItems : "") +
                (mechanismItems.Length > 0 ? "\n机制候选：" + mechanismItems :
                    Json.A(profile, "preferredItemIds").Any() ? "\n暂无该英雄有足够样本的机制候选装备。" :
                    "\n此条件没有可通用指定的装备；维持常规出装供比较。") +
                "\n" + path : path;
            foreach (object raw in Json.A(detail, "conditionalEffects")) {
                var row = Json.Obj(raw);
                buildText += "\n需按英雄核对 · " + Json.S(row, "name") + "：" + Json.S(row, "effect");
            }
            string noDirect = String.Join("、", Json.A(detail, "noDirectOwned").Select(Convert.ToString));
            if (noDirect.Length > 0) buildText += "\n已审查、暂无明确装备转向：" + noDirect;
            Find<TextBlock>("BuildPath").Text = buildText;
            RenderEquipmentCards(detail);
            var top = Find<WrapPanel>("TopAugments"); top.Children.Clear();
            foreach (object raw in Json.A(detail, "topAugments")) {
                var row = Json.Obj(raw);
                top.Children.Add(new Border { Child = Text(Json.S(row, "name") + "  " + Json.Percent(Json.N(row, "winRate")), 11, "#BBCAE0", false),
                    CornerRadius = new CornerRadius(5), Padding = new Thickness(9, 7, 9, 7), Margin = new Thickness(0, 0, 7, 7), Background = BrushOf("#1D2B41") });
            }
        }
        void RenderEquipmentCards(Dictionary<string, object> result) {
            var grid = Find<UniformGrid>("ItemCards"); grid.Children.Clear();
            var values = Json.A(result, result.ContainsKey("regularItems") ? "regularItems" : "items");
            foreach (var row in values.Select(Json.Obj).Take(3)) grid.Children.Add(EquipmentSuggestion(row, false));
            var synergyGrid = Find<UniformGrid>("SynergyItemCards"); synergyGrid.Children.Clear();
            foreach (var row in Json.A(result, "synergyItems").Select(Json.Obj).Take(3)) synergyGrid.Children.Add(EquipmentSuggestion(row, true));
            Find<StackPanel>("SynergyItemSection").Visibility = synergyGrid.Children.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            synergyGrid.Columns = Math.Max(1, synergyGrid.Children.Count);
        }
        void EmptyChoices() {
            var cards = Find<UniformGrid>("ChoiceCards"); cards.Children.Clear();
            for (int i = 1; i <= 3; i++) cards.Children.Add(ChoiceCard(Json.D("slot", i, "name", "等待识别", "reason", "导入截图或切回游戏识别", "matched", false), false));
            Find<TextBlock>("ResultMeta").Text = "等待识别";
        }
        Border ChoiceCard(Dictionary<string, object> c, bool compact) {
            bool recommended = Json.B(c, "recommended"), matched = Json.B(c, "matched"), stats = Json.B(c, "hasStats");
            var conditional = Json.Obj(Json.Get(c, "conditional")); bool hasConditional = conditional.Count > 0;
            bool hasOwned = ownedAugments.Count > 0;
            var p = new StackPanel();
            string color = recommended ? "#83F1C6" : "#D5DFF1";
            Add(p, "0" + Json.S(c, "slot") + "  /  " + (recommended ? "优先参考" : matched ? Json.S(c, "rarity") : "强化选项"), 10, recommended ? "#80E6BD" : "#879DBB", true, 0);
            Add(p, Json.S(c, "name"), compact ? 17 : 20, color, true, 12);
            Add(p, hasConditional ? Json.Percent(Json.N(conditional, "winRate")) + " 条件胜率" :
                stats ? Json.Percent(Json.N(c, "winRate")) + (hasOwned ? " 单项胜率" : " 胜率") : "—",
                compact ? 19 : 25, color, true, compact ? 10 : 17);
            if (hasConditional) {
                double delta = Json.N(conditional, "delta") * 100;
                Add(p, (delta >= 0 ? "+" : "") + delta.ToString("0.0") + " 个百分点 · 较已选基线 " +
                    Json.Percent(Json.N(conditional, "baselineWinRate")), 10, "#9DB1CC", false, 4);
                Add(p, Json.Count(Json.N(conditional, "games")) + " 场联合样本 · " +
                    (Json.B(conditional, "reliable") ? "样本充足" : "样本有限"), 11, "#96ADC9", false, 9);
            } else if (stats) {
                double delta = Json.N(c, "delta") * 100;
                if (!hasOwned && Json.Get(c, "delta") != null) Add(p, (delta >= 0 ? "+" : "") + delta.ToString("0.0") + " 个百分点 · 较英雄均值", 10, "#9DB1CC", false, 4);
                else if (hasOwned) Add(p, "未计入本局已选海克斯", 10, "#D6B47F", false, 4);
                Add(p, Samples(c) + " · " + Json.S(c, "reliability"), 11, Json.N(c, "games") < 30 ? "#F3BC78" : "#96ADC9", false, 9);
            }
            Add(p, Json.S(c, "reason"), 11, "#95AEC7", false, 12);
            var clue = Json.Obj(Json.Get(c, "trioClue"));
            if (clue.Count > 0) Add(p, "已公布三件套共同出现 " + Json.S(clue, "publishedTrioRows") + " 组；非条件胜率", 10, "#8398AF", false, 5);
            if (!matched && Json.S(c, "raw").Length > 0) Add(p, "读到：" + Json.S(c, "raw"), 10, "#D5B381", false, 7);
            return new Border { Child = p, Background = BrushOf(recommended ? "#153A35" : "#151F32"),
                BorderBrush = BrushOf(recommended ? "#489F82" : "#2B3A54"), BorderThickness = new Thickness(recommended ? 1.5 : 1),
                CornerRadius = new CornerRadius(11), Padding = new Thickness(compact ? 13 : 16),
                Margin = compact ? new Thickness(0, 0, 0, 8) : new Thickness(0, 0, 8, 0), MinHeight = compact ? 0 : 239 };
        }
        void RenderResult(Dictionary<string, object> result) {
            if (Json.S(result, "hero") != hero || closing) return;
            lastResult = result;
            var cards = Find<UniformGrid>("ChoiceCards"); cards.Children.Clear();
            int index = 0;
            foreach (object raw in Json.A(result, "choices")) {
                var c = Json.Obj(raw); cards.Children.Add(ChoiceCard(c, false));
                currentChoices[index] = Json.B(c, "matched") ? Json.S(c, "name") : Json.S(c, "raw");
                Find<ComboBox>("Manual" + (++index)).Text = currentChoices[index - 1];
            }
            string source = Json.S(result, "source");
            string label = source == "demo" ? "示例 · 未识别屏幕" : source == "image" ? "截图导入" : source == "manual" ? "手动输入" : "游戏截图";
            string effectivePreset = Json.S(Json.Obj(Json.Get(result, "ocrDebug")), "preset");
            Find<TextBlock>("ResultMeta").Text = label + " · " + Json.S(result, "time") +
                (effectivePreset.Length > 0 ? " · 布局 " + effectivePreset : "");
            string message = Json.S(result, "message");
            if (message.Length == 0) message = source == "demo" ? "这是该英雄三个常见强化的演示结果，不代表你当前游戏中的选项。" :
                "已匹配 " + Json.S(result, "matchedCount") + " / 3 项。" + (Json.B(result, "autoTrackingReady") && automatic
                    ? "游戏内点击选项后会自动记入本局。"
                    : Json.B(result, "complete") ? "识别结果已写入日志。" : "正在自动重试未识别的卡片，诊断已保存。");
            Status(message); RenderOverlay();
        }
        bool SameScreenChoices(Dictionary<string, object> result) {
            if (lastResult == null || Json.S(lastResult, "hero") != Json.S(result, "hero") ||
                    Json.S(lastResult, "source") != "screen" || Json.S(result, "source") != "screen") return false;
            var previous = Json.A(lastResult, "choices").Select(Json.Obj).ToArray();
            var current = Json.A(result, "choices").Select(Json.Obj).ToArray();
            if (previous.Length != 3 || current.Length != 3 ||
                    Json.B(lastResult, "autoTrackingReady") != Json.B(result, "autoTrackingReady")) return false;
            for (int i = 0; i < 3; i++)
                if (Json.S(previous[i], "name") != Json.S(current[i], "name") ||
                    Json.B(previous[i], "matched") != Json.B(current[i], "matched")) return false;
            return true;
        }
    }

}
