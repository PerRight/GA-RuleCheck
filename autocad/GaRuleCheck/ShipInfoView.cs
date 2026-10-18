// '선박 정보' 탭: DWGPROPS 대신 여기서 GARC_* 특성과 흘수 조건을 편집한다.
// 값은 문자열로 도면에 저장하고, 해석·검증은 엔진이 한다(잘못된 값 → 검토 불가로 표시).
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Linq;
using System.Windows.Forms;

namespace GaRuleCheck
{
    public sealed class ShipInfoView : Panel
    {
        /// AP / FP / BASE — 도면에서 점을 찍어 값을 채운다.
        public event Action<string>? PickRequested;
        public event Action? EyeRequested;
        public event Action<Dictionary<string, string>>? SaveRequested;
        public event Action? ReloadRequested;
        public event Action? SetupRequested;

        private readonly Dictionary<string, TextBox> _boxes = new();
        private readonly DataGridView _conds;
        private readonly ComboBox _units;
        private readonly Label _msg;

        public ShipInfoView()
        {
            BackColor = Theme.Bg;
            AutoScroll = true;
            Padding = new Padding(10);

            var flow = new FlowLayoutPanel
            {
                Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown,
                WrapContents = false, BackColor = Theme.Bg,
            };
            Controls.Add(flow);

            flow.Controls.Add(Theme.Label("선박 정보 (도면에 저장)", Theme.Title));
            flow.Controls.Add(Theme.Label("길이·위치는 m. AP·FP·기선은 '도면에서'로 점을 찍으면 채워집니다.", Theme.Small, Theme.Muted));

            var grid = new TableLayoutPanel
            {
                ColumnCount = 3, AutoSize = true, BackColor = Theme.Bg, Margin = new Padding(0, 8, 0, 0),
            };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 150));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 110));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            var tip = new ToolTip();
            int row = 0;
            foreach (var (key, label, hint) in DrawingProps.Fields)
            {
                var l = Theme.Label(label);
                l.Margin = new Padding(0, 6, 4, 0);
                tip.SetToolTip(l, hint);
                var tb = new TextBox
                {
                    Width = 104, BackColor = Theme.Input, ForeColor = Theme.Text, BorderStyle = BorderStyle.FixedSingle,
                    Font = Theme.Body, Margin = new Padding(0, 3, 4, 3),
                };
                tip.SetToolTip(tb, hint);
                _boxes[key] = tb;
                grid.Controls.Add(l, 0, row);
                grid.Controls.Add(tb, 1, row);
                string? pick = key switch { "GARC_X_AP" => "AP", "GARC_X_FP" => "FP", "GARC_Z_BASE" => "BASE", _ => null };
                if (pick != null)
                {
                    var b = Theme.FlatButton("도면에서");
                    b.Click += (_, _) => PickRequested?.Invoke(pick);
                    grid.Controls.Add(b, 2, row);
                }
                row++;
            }
            var ul = Theme.Label("도면 단위");
            ul.Margin = new Padding(0, 6, 4, 0);
            _units = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList, Width = 104, BackColor = Theme.Input, ForeColor = Theme.Text,
                FlatStyle = FlatStyle.Flat, Font = Theme.Body,
            };
            _units.Items.AddRange(new object[] { "도면 설정 따름", "mm", "cm", "m" });
            _units.SelectedIndex = 0;
            tip.SetToolTip(_units, "보통 '도면 설정 따름'($INSUNITS). 단위가 미지정 도면이면 직접 고르세요.");
            grid.Controls.Add(ul, 0, row);
            grid.Controls.Add(_units, 1, row);
            flow.Controls.Add(grid);

            // 조종위치
            var eyeRow = new FlowLayoutPanel { AutoSize = true, BackColor = Theme.Bg, Margin = new Padding(0, 8, 0, 0) };
            var eyeBtn = Theme.FlatButton("조종위치 지정 (GA_EYE 점)", Theme.Info);
            eyeBtn.Click += (_, _) => EyeRequested?.Invoke();
            eyeRow.Controls.Add(eyeBtn);
            eyeRow.Controls.Add(Theme.Label("선교 갑판 높이의 조종 위치를 클릭", Theme.Small, Theme.Muted));
            flow.Controls.Add(eyeRow);

            // 흘수 조건
            flow.Controls.Add(Theme.Label("흘수 조건 (AP·FP 위치, 기선 기준 m)", Theme.Bold));
            _conds = new DataGridView
            {
                Width = 330, Height = 130, BackgroundColor = Theme.Input, ForeColor = Theme.Text, GridColor = Theme.Line,
                BorderStyle = BorderStyle.None, RowHeadersVisible = false, AllowUserToAddRows = true,
                AllowUserToDeleteRows = true, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
                EnableHeadersVisualStyles = false, Font = Theme.Body,
            };
            _conds.ColumnHeadersDefaultCellStyle.BackColor = Theme.Card;
            _conds.ColumnHeadersDefaultCellStyle.ForeColor = Theme.Muted;
            _conds.DefaultCellStyle.BackColor = Theme.Input;
            _conds.DefaultCellStyle.ForeColor = Theme.Text;
            _conds.DefaultCellStyle.SelectionBackColor = Theme.CardHover;
            _conds.Columns.Add("name", "이름");
            _conds.Columns.Add("aft", "선미 흘수");
            _conds.Columns.Add("fwd", "선수 흘수");
            _conds.Columns[0].FillWeight = 160;
            flow.Controls.Add(_conds);
            flow.Controls.Add(Theme.Label("행 삭제: 행 선택 후 Delete. 밸러스트 도착(선미 트림)이 보통 최악입니다.", Theme.Small, Theme.Muted));

            _msg = Theme.Label("", Theme.Small, Theme.Warn);
            flow.Controls.Add(_msg);

            var actions = new FlowLayoutPanel { AutoSize = true, BackColor = Theme.Bg, Margin = new Padding(0, 8, 0, 0) };
            var save = Theme.FlatButton("저장 후 검사", Theme.Pass);
            save.Click += (_, _) => Save();
            var reload = Theme.FlatButton("도면에서 다시 읽기");
            reload.Click += (_, _) => ReloadRequested?.Invoke();
            var setup = Theme.FlatButton("레이어·스타일 만들기");
            setup.Click += (_, _) => SetupRequested?.Invoke();
            actions.Controls.Add(save);
            actions.Controls.Add(reload);
            actions.Controls.Add(setup);
            flow.Controls.Add(actions);
        }

        public void Load(Dictionary<string, string> props)
        {
            foreach (var (key, tb) in _boxes)
                tb.Text = props.TryGetValue(key, out var v) ? v : "";
            var u = props.TryGetValue("GARC_UNITS", out var uu) ? uu.ToLowerInvariant() : "";
            _units.SelectedIndex = u switch { "mm" => 1, "cm" => 2, "m" => 3, _ => 0 };
            _conds.Rows.Clear();
            foreach (var c in DrawingProps.ParseConditions(props.TryGetValue("GARC_CONDITIONS", out var cs) ? cs : ""))
                _conds.Rows.Add(c.Name, c.TAft, c.TFwd);
            _msg.Text = "";
        }

        public void SetField(string key, string value)
        {
            if (_boxes.TryGetValue(key, out var tb)) tb.Text = value;
        }

        private void Save()
        {
            var errors = new List<string>();
            var values = new Dictionary<string, string>();
            foreach (var (key, tb) in _boxes)
            {
                var t = tb.Text.Trim();
                tb.BackColor = Theme.Input;
                if (key == "GARC_SCALE")
                {
                    // '1/1000'·'1:1000'·'1000' — 해석·범위 검사는 엔진이 한다(여기서는 형식만)
                    if (t.Length > 0 && !System.Text.RegularExpressions.Regex.IsMatch(t.Replace(" ", ""), @"^(1[/:])?\d+(\.\d+)?$"))
                    { errors.Add("도면 축척은 1/N 형식"); tb.BackColor = Color.FromArgb(90, Theme.Fail); }
                }
                else if (key == "GARC_KEEL_LAID")
                {
                    if (t.Length > 0 && !DateTime.TryParseExact(t, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _))
                    { errors.Add("용골 거치일은 YYYY-MM-DD"); tb.BackColor = Color.FromArgb(90, Theme.Fail); }
                }
                else if (t.Length > 0 && !IsFiniteNumber(t))
                {
                    errors.Add($"{key} 숫자 아님");
                    tb.BackColor = Color.FromArgb(90, Theme.Fail);
                }
                values[key] = t;
            }
            values["GARC_UNITS"] = _units.SelectedIndex switch { 1 => "mm", 2 => "cm", 3 => "m", _ => "" };

            var rows = new List<ConditionRow>();
            foreach (DataGridViewRow r in _conds.Rows)
            {
                if (r.IsNewRow) continue;
                string name = $"{r.Cells[0].Value}".Trim(), aft = $"{r.Cells[1].Value}".Trim(), fwd = $"{r.Cells[2].Value}".Trim();
                if (name.Length == 0 && aft.Length == 0 && fwd.Length == 0) continue;
                if (name.Contains(':') || name.Contains(';')) errors.Add("조건 이름에 : ; 는 쓸 수 없음");
                if (!IsFiniteNumber(aft) || !IsFiniteNumber(fwd))
                    errors.Add($"흘수 조건 '{name}' 값 확인");
                rows.Add(new ConditionRow(name.Length == 0 ? "조건" : name, aft, fwd));
            }
            values["GARC_CONDITIONS"] = DrawingProps.FormatConditions(rows);

            if (errors.Count > 0)
            {
                _msg.ForeColor = Theme.Fail;
                _msg.Text = "저장 안 함: " + string.Join(" / ", errors.Distinct());
                return;
            }
            _msg.ForeColor = Theme.Pass;
            _msg.Text = $"저장함 {DateTime.Now:HH:mm:ss} — 다시 검사합니다.";
            SaveRequested?.Invoke(values);
        }

        /// "NaN"·"Infinity"도 double.TryParse는 성공하므로 유한 여부까지 본다.
        /// (값의 범위·의미 검증은 엔진이 한다 — 여기서는 저장 전에 명백한 입력 실수만 막는다)
        private static bool IsFiniteNumber(string s) =>
            double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) && double.IsFinite(v);
    }
}
