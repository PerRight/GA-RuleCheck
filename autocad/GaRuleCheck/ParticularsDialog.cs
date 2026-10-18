// 실도면 연결 1단계: 주요 치수·축척·흘수 조건 확인 창.
// 도면 문자에서 엔진이 찾은 후보(/v1/particulars)를 보여 주고, 사람이 확인·수정한 값만 도면 특성(GARC_*)에 쓴다.
// 이미 입력된 값이 있으면 그 값을 먼저 보여 준다(조용히 덮어쓰지 않음). L(ICLL)·밸러스트 흘수는 도면에 없으므로 비워 둔다.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Linq;
using System.Windows.Forms;

namespace GaRuleCheck
{
    public sealed class ParticularsDialog : Form
    {
        private readonly Dictionary<string, TextBox> _boxes = new();
        private readonly TextBox _conditions;
        private readonly ComboBox? _units;

        /// 확인 후 도면 특성에 쓸 값(빈 값 = 지우지 않고 그대로 둔다 — 호출자가 빈 값은 건너뜀)
        public Dictionary<string, string> Result { get; } = new();

        private static readonly (string Key, string Label, string Cand, string Hint)[] Rows =
        {
            ("GARC_SCALE", "도면 축척", "scale", "시트에 1/N로 그렸으면 1/N (실도면 대부분 1/1000)"),
            ("GARC_LOA", "LOA 전장 [m]", "loa", "가시거리 규칙의 L"),
            ("GARC_LBP", "LBP 수선간 길이 [m]", "lbp", "참고값 — AP–FP 간격과 대조"),
            ("GARC_L_LL", "L 만재흘수선 길이 [m]", "", "충돌격벽 규칙의 L(ICLL) — 선급 계산서 값. 도면에 없음"),
            ("GARC_SHIP_TYPE", "선종", "ship_type", ""),
            ("GARC_KEEL_LAID", "용골 거치일", "", "YYYY-MM-DD (없으면 1998-07-01 이후로 가정)"),
        };

        public ParticularsDialog(ParticularsResponse? found, IReadOnlyDictionary<string, string> current, bool unitless, string drawingName)
        {
            Text = "실도면 연결 — 주요 치수 확인";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent;
            MaximizeBox = MinimizeBox = false;
            ShowInTaskbar = false;
            BackColor = Theme.Bg;
            ForeColor = Theme.Text;
            Font = Theme.Body;
            ClientSize = new Size(620, 600);
            KeyPreview = true;

            var flow = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false,
                Padding = new Padding(16, 14, 16, 8), AutoScroll = true,
            };
            Controls.Add(flow);
            flow.Controls.Add(Theme.Label($"{drawingName} — 도면 문자에서 읽은 값", Theme.Title));
            flow.Controls.Add(Theme.Label(found == null
                ? "엔진에 연결하지 못해 도면 문자를 읽지 못했습니다. 값을 직접 입력하세요."
                : "가운데 열이 도면에서 찾은 값입니다. 오른쪽 칸의 값이 도면 특성에 저장됩니다(이미 입력된 값이 있으면 그 값을 보여 줍니다).",
                Theme.Small, Theme.Muted));

            var grid = new TableLayoutPanel { ColumnCount = 3, AutoSize = true, Margin = new Padding(0, 8, 0, 0), BackColor = Color.Transparent };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 170));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 170));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 230));
            grid.Controls.Add(Head("항목"), 0, 0);
            grid.Controls.Add(Head("도면에서 찾은 값"), 1, 0);
            grid.Controls.Add(Head("사용할 값"), 2, 0);
            int row = 1;
            if (unitless)
            {
                _units = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 210, BackColor = Theme.Input, ForeColor = Theme.Text, FlatStyle = FlatStyle.Flat };
                _units.Items.AddRange(new object[] { "mm", "cm", "m" });
                var cu = current.TryGetValue("GARC_UNITS", out var u) ? u.Trim().ToLowerInvariant() : "";
                _units.SelectedIndex = cu == "cm" ? 1 : cu == "m" ? 2 : 0;
                grid.Controls.Add(Theme.Label("도면 단위", Theme.Body), 0, row);
                grid.Controls.Add(Theme.Label("단위 없음($INSUNITS=0)", Theme.Small, Theme.Warn), 1, row);
                grid.Controls.Add(_units, 2, row);
                row++;
            }
            foreach (var (key, label, cand, hint) in Rows)
            {
                string candText = "";
                if (found != null && cand.Length > 0 && found.Items.TryGetValue(cand, out var it))
                {
                    candText = it.Status switch
                    {
                        "found" => key == "GARC_SCALE" ? "1/" + it.ValueText : it.ValueText,
                        "conflict" => "값이 여러 개 — 직접 확인",
                        _ => "못 찾음",
                    };
                }
                else if (key == "GARC_L_LL")
                    candText = found != null && found.Items.TryGetValue("lbp", out var lbp) && lbp.Status == "found" ? $"없음 (LBP {lbp.ValueText} m 참고)" : "없음";
                var box = new TextBox
                {
                    Width = 220, BackColor = Theme.Input, ForeColor = Theme.Text, BorderStyle = BorderStyle.FixedSingle, Font = Theme.Body,
                };
                if (current.TryGetValue(key, out var cur) && !string.IsNullOrWhiteSpace(cur)) box.Text = cur;
                else if (found != null && found.SuggestedProps.TryGetValue(key, out var sug)) box.Text = sug;
                else if (key == "GARC_SHIP_TYPE" && found != null && found.Items.TryGetValue("ship_type", out var st) && st.Status == "found") box.Text = st.ValueText;
                _boxes[key] = box;
                var lab = Theme.Label(label, Theme.Body);
                var c = Theme.Label(candText, Theme.Small, candText.StartsWith("값이") ? Theme.Warn : Theme.Muted);
                var tip = new ToolTip();
                if (hint.Length > 0) { tip.SetToolTip(box, hint); tip.SetToolTip(lab, hint); }
                grid.Controls.Add(lab, 0, row);
                grid.Controls.Add(c, 1, row);
                grid.Controls.Add(box, 2, row);
                row++;
            }
            flow.Controls.Add(grid);

            flow.Controls.Add(Caption("흘수 조건 (이름:선미 흘수/선수 흘수, 한 줄에 하나, 기선 기준 m)"));
            _conditions = new TextBox
            {
                Width = 570, Height = 110, Multiline = true, AcceptsReturn = true, ScrollBars = ScrollBars.Vertical,
                BackColor = Theme.Input, ForeColor = Theme.Text, BorderStyle = BorderStyle.FixedSingle, Font = Theme.Body,
            };
            if (current.TryGetValue("GARC_CONDITIONS", out var conds) && !string.IsNullOrWhiteSpace(conds))
                _conditions.Text = string.Join(Environment.NewLine, conds.Split(';').Select(x => x.Trim()).Where(x => x.Length > 0));
            else
            {
                var lines = (found?.SuggestedConditions ?? new()).Select(cd =>
                    $"{cd.Name}:{cd.TAft.ToString("0.###", CultureInfo.InvariantCulture)}/{cd.TFwd.ToString("0.###", CultureInfo.InvariantCulture)}").ToList();
                lines.Add("밸러스트 도착:/");   // 비워 두면 엔진이 '검토 불가'로 멈춘다 — 실제 흘수를 넣거나 줄을 지우세요
                _conditions.Text = string.Join(Environment.NewLine, lines);
            }
            flow.Controls.Add(_conditions);
            var notes = new List<string>
            {
                "설계·구조 흘수(등흘수)는 도면 표에서 가져온 후보입니다.",
                "가시거리는 보통 밸러스트(선수 흘수가 작은) 상태가 가장 불리합니다 — 선미/선수 흘수를 넣으세요. 모르면 그 줄을 지우되, 그러면 그 상태는 판정하지 않습니다.",
            };
            if (found != null) notes.AddRange(found.HumanInputsKo.Select(h => "사람 입력: " + h));
            var nl = Theme.Label(string.Join("\n", notes.Select(n => "· " + n)), Theme.Small, Theme.Muted);
            nl.MaximumSize = new Size(570, 0);
            flow.Controls.Add(nl);

            var buttons = new FlowLayoutPanel
            {
                Dock = DockStyle.Bottom, FlowDirection = FlowDirection.RightToLeft, Height = 46,
                Padding = new Padding(12, 8, 12, 8), BackColor = Theme.Card,
            };
            var ok = Theme.FlatButton("적용", Theme.Info);
            var skip = Theme.FlatButton("이 단계 건너뛰기");
            ok.Click += (_, _) =>
            {
                foreach (var (k, b) in _boxes) Result[k] = b.Text.Trim();
                Result["GARC_CONDITIONS"] = string.Join("; ", _conditions.Lines.Select(x => x.Trim()).Where(x => x.Length > 0));
                if (_units != null) Result["GARC_UNITS"] = _units.SelectedItem?.ToString() ?? "mm";
                DialogResult = DialogResult.OK;
            };
            skip.Click += (_, _) => DialogResult = DialogResult.Cancel;
            buttons.Controls.Add(ok);
            buttons.Controls.Add(skip);
            Controls.Add(buttons);
            AcceptButton = ok;
            CancelButton = skip;
        }

        private static Label Head(string t)
        {
            var l = Theme.Label(t, Theme.Bold, Theme.Muted);
            l.Margin = new Padding(0, 0, 0, 4);
            return l;
        }

        private static Label Caption(string text)
        {
            var l = Theme.Label(text, Theme.Bold);
            l.Margin = new Padding(0, 12, 0, 3);
            return l;
        }
    }
}
