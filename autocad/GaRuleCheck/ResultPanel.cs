// 팔레트 화면 (WinForms, 코드로 구성, AutoCAD 다크 테마).
//   상단: 전체 판정 + rev·도면·엔진 상태, 도구 버튼(검사·표시·리포트)
//   탭:   검토(규칙 카드) / 선박 정보(특성 편집) / 이력(revision 타임라인)
//   하단: 면책 문구 고정 — 충족(파랑)은 '규칙 후보 기준 충족'일 뿐 승인 아님
//   탭 줄 오른쪽: '미충족만 보기' 필터(팔레트·떠 있는 패널이 함께 바뀜, ui.json에 기억)
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;

namespace GaRuleCheck
{
    public sealed class ResultPanel : UserControl
    {
        public event Action? RecheckRequested;
        public event Action? StartRequested;
        /// 실도면 연결 마법사(GARCLINK) 요청 — 준비 상태 카드·시작 화면의 버튼
        public event Action? LinkRequested;
        public event Action? ToggleOverlayRequested;
        public event Action? ReportRequested;
        public event Action<List<string>>? FocusRequested;
        public event Action<List<string>, bool>? HoverRequested;
        public event Action<RuleResultDto>? FitRequested;
        public event Action? LargeLabelsRequested;
        public event Action<ElementCheckDto, bool>? ElementHoverRequested;
        public event Action<ElementCheckDto>? ElementFocusRequested;

        public ShipInfoView Ship { get; } = new() { Dock = DockStyle.Fill };

        private readonly Panel _header = new() { Dock = DockStyle.Top, Height = 74, BackColor = Theme.Card, Padding = new Padding(12, 8, 8, 6) };
        private readonly StatusDot _dot = new() { Location = new Point(12, 12) };
        // 긴 전체 판정 문구가 잘리지 않게 두 줄까지 줄바꿈(짧으면 큰 글씨)
        private readonly Label _overall = new() { AutoSize = false, Location = new Point(34, 4), Height = 36, Font = Theme.Title, ForeColor = Theme.Text, AutoEllipsis = true };
        private readonly Label _sub = new() { AutoSize = false, Location = new Point(34, 44), Height = 18, Font = Theme.Small, ForeColor = Theme.Muted, AutoEllipsis = true };
        private readonly Button _failOnly;
        private CheckResponse? _last;
        private string _lastDoc = "";
        private readonly Label _engine = new() { AutoSize = true, Font = Theme.Small, ForeColor = Theme.Muted, Anchor = AnchorStyles.Top | AnchorStyles.Right };

        private readonly FlowLayoutPanel _tools = new() { Dock = DockStyle.Top, Height = 36, BackColor = Theme.Bg, Padding = new Padding(10, 6, 6, 0) };
        private readonly Button _overlayBtn;
        private readonly Button _largeBtn;
        private readonly FlowLayoutPanel _tabs = new() { Dock = DockStyle.Top, Height = 30, BackColor = Theme.Bg, Padding = new Padding(10, 0, 0, 0) };
        private readonly Panel _content = new() { Dock = DockStyle.Fill, BackColor = Theme.Bg };
        private readonly FlowLayoutPanel _cards = new() { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoScroll = true, BackColor = Theme.Bg, Padding = new Padding(10, 8, 10, 8) };
        private readonly ListView _history = new()
        {
            Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, HeaderStyle = ColumnHeaderStyle.Nonclickable,
            BackColor = Theme.Bg, ForeColor = Theme.Text, BorderStyle = BorderStyle.None, Font = Theme.Body,
        };
        private readonly Label _footer = new()
        {
            Dock = DockStyle.Bottom, Height = 38, Font = Theme.Small, ForeColor = Theme.Muted, BackColor = Theme.Card,
            Padding = new Padding(10, 3, 10, 3),
            Text = "규칙 엔진의 후보 판정입니다. 선급 승인이 아니며 최종 판단은 설계자 검토로 합니다.",
        };
        private readonly Dictionary<string, Button> _tabButtons = new();
        private string _tab = "review";

        public ResultPanel()
        {
            BackColor = Theme.Bg;
            Font = Theme.Body;

            _header.Controls.AddRange(new Control[] { _dot, _overall, _sub, _engine });
            _header.Resize += (_, _) =>
            {
                _engine.Location = new Point(_header.Width - _engine.Width - 10, 46);
                _overall.Width = _header.Width - 44;
                FitOverallFont();
                _sub.Width = Math.Max(_engine.Left - 40, 60);
            };

            var check = Theme.FlatButton("다시 검사", Theme.Info);
            check.Click += (_, _) => RecheckRequested?.Invoke();
            _overlayBtn = Theme.FlatButton("도면 표시: 켜짐");
            _overlayBtn.Click += (_, _) => ToggleOverlayRequested?.Invoke();
            var report = Theme.FlatButton("리포트");
            report.Click += (_, _) => ReportRequested?.Invoke();
            _largeBtn = Theme.FlatButton("라벨 확대");
            _largeBtn.Click += (_, _) => LargeLabelsRequested?.Invoke();
            var tip = new ToolTip();
            tip.SetToolTip(_largeBtn, "도면 위 라벨 글자·선 두께·표식 크기만 2.5배로 키웁니다(프로젝터·화면 공유용). 판정과 도면 객체는 바뀌지 않습니다.");
            tip.SetToolTip(_overlayBtn, "위반 표시·시선·라벨을 켜거나 끕니다 (판정은 계속).");
            tip.SetToolTip(report, "근거·측면도·이력을 HTML로 저장하고 엽니다.");
            _tools.Controls.AddRange(new Control[] { check, _overlayBtn, _largeBtn, report });

            foreach (var (id, text) in new[] { ("review", "검토"), ("ship", "선박 정보"), ("history", "이력") })
            {
                var b = new Button
                {
                    Text = text, FlatStyle = FlatStyle.Flat, AutoSize = true, Font = Theme.Bold, ForeColor = Theme.Muted,
                    BackColor = Theme.Bg, Margin = new Padding(0, 0, 4, 0), Cursor = Cursors.Hand, UseVisualStyleBackColor = false,
                };
                b.FlatAppearance.BorderSize = 0;
                b.FlatAppearance.MouseOverBackColor = Theme.Card;
                b.Paint += (s, e) =>
                {
                    if (_tab != id) return;
                    using var br = new SolidBrush(Theme.Info);
                    e.Graphics.FillRectangle(br, 4, b.Height - 3, b.Width - 8, 3);
                };
                b.Click += (_, _) => ShowTab(id);
                _tabButtons[id] = b;
                _tabs.Controls.Add(b);
            }

            _failOnly = new Button
            {
                Text = "미충족만 보기", FlatStyle = FlatStyle.Flat, AutoSize = true, Font = Theme.Small, ForeColor = Theme.Muted,
                BackColor = Theme.Bg, Margin = new Padding(12, 3, 0, 0), Cursor = Cursors.Hand, UseVisualStyleBackColor = false,
                AccessibleRole = AccessibleRole.CheckButton,
            };
            _failOnly.FlatAppearance.BorderColor = Theme.Line;
            _failOnly.Click += (_, _) => UiState.SetFailOnly(!UiState.Current.FailOnly);
            tip.SetToolTip(_failOnly, "미충족(빨강) 규칙·객체만 보여 줍니다. 확인 필요 항목은 숨긴 개수를 표시합니다.");
            _tabs.Controls.Add(_failOnly);
            UiState.FailOnlyChanged += OnFailOnlyChanged;
            SyncFailOnlyButton();

            _history.Columns.Add("rev", 44);
            _history.Columns.Add("시각", 64);
            _history.Columns.Add("변경 · 판정 변화", 400);
            _cards.Resize += (_, _) => FitCards();

            Controls.Add(_content);
            Controls.Add(_footer);
            Controls.Add(_tabs);
            Controls.Add(_tools);
            Controls.Add(_header);
            ShowTab("review");
            SetIdle("GARC로 검사를 시작하세요.");
            SetEngine(null);
        }

        public void ShowTab(string id)
        {
            _tab = id;
            _content.Controls.Clear();
            _content.Controls.Add(id switch { "ship" => (Control)Ship, "history" => _history, _ => _cards });
            foreach (var (k, b) in _tabButtons) { b.ForeColor = k == id ? Theme.Text : Theme.Muted; b.Invalidate(); }
        }

        public void SetOverlayState(bool on) => _overlayBtn.Text = on ? "도면 표시: 켜짐" : "도면 표시: 꺼짐";

        private void OnFailOnlyChanged()
        {
            if (IsDisposed) return;
            SyncFailOnlyButton();
            if (_last != null) ShowResult(_last, _lastDoc, record: false);
        }

        private void SyncFailOnlyButton()
        {
            bool on = UiState.Current.FailOnly;
            _failOnly.Text = on ? "미충족만 보기 ●" : "미충족만 보기";
            _failOnly.ForeColor = on ? Theme.Text : Theme.Muted;
            _failOnly.BackColor = on ? Theme.Fail : Theme.Bg;
            _failOnly.AccessibleName = "미충족만 보기 " + (on ? "켜짐" : "꺼짐");
        }

        private void SetOverall(string text)
        {
            _overall.Text = text;
            FitOverallFont();
        }

        private void FitOverallFont()
        {
            if (_overall.Width <= 0) return;
            bool fits = TextRenderer.MeasureText(_overall.Text, Theme.Title).Width <= _overall.Width;
            _overall.Font = fits ? Theme.Title : Theme.Bold;
        }

        public void SetLargeLabelsState(bool on)
        {
            _largeBtn.BackColor = on ? Theme.Info : Theme.Card;
            _largeBtn.Text = on ? "라벨 확대 ●" : "라벨 확대";
        }

        public void SetEngine(bool? up)
        {
            _engine.Text = up switch { true => "● 엔진 연결", false => "● 엔진 끊김", _ => "● 엔진 대기" };
            _engine.ForeColor = up switch { true => Theme.Pass, false => Theme.Fail, _ => Theme.Muted };
            _header.PerformLayout();
            _engine.Location = new Point(_header.Width - _engine.Width - 10, 46);
        }

        public void SetIdle(string text, string? docName = null, bool showSetup = false)
        {
            _dot.Color = Theme.Skip;
            _last = null;
            SetOverall(text);
            _sub.Text = docName ?? "";
            _cards.Controls.Clear();
            var box = new Panel { BackColor = Theme.Card, Padding = new Padding(14), Margin = new Padding(0, 0, 0, 8), Height = 120 };
            var t = Theme.Label("GA 도면 변경을 감지해 관련 규칙만 다시 검사합니다.", Theme.Body);
            t.Location = new Point(14, 14);
            var s = Theme.Label("1) 선박 정보 탭에서 LOA·L·AP·FP·흘수 입력\n2) GA_ 레이어에 선체·조종위치·장애물·충돌격벽 그리기\n3) 검사 시작\n실제 도면(GA_ 레이어 없음, 1/1000 축척 등)은 [실도면 연결]로 레이어를 바꾸지 않고 연결합니다.", Theme.Small, Theme.Muted);
            s.Location = new Point(14, 38);
            box.Height = 150;
            var b = Theme.FlatButton(showSetup ? "레이어 만들고 시작" : "검사 시작", Theme.Info);
            b.Location = new Point(14, 112);
            b.Click += (_, _) => StartRequested?.Invoke();
            var link = Theme.FlatButton("실도면 연결");
            link.Location = new Point(b.Right + 8, 112);
            link.Click += (_, _) => LinkRequested?.Invoke();
            box.Controls.AddRange(new Control[] { t, s, b, link });
            _cards.Controls.Add(box);
            FitCards();
        }

        public void ShowEngineDown(string message)
        {
            _dot.Color = Theme.Warn;
            _last = null;
            SetOverall("검토 불가 — 엔진 연결 안 됨");
            _sub.Text = message;
            SetEngine(false);
            _cards.Controls.Clear();
            var c = Theme.Label(message + "\n\n엔진 폴더에서 'python -m garc serve' 실행 후 '다시 검사'.", Theme.Body, Theme.Warn);
            c.MaximumSize = new Size(Math.Max(_cards.ClientSize.Width - 30, 200), 0);
            _cards.Controls.Add(c);
            Overlay.Clear();
        }

        public void ShowResult(CheckResponse r, string docName, bool record = true)
        {
            SetEngine(true);
            _last = r;
            _lastDoc = docName;
            _dot.Color = Theme.Status(r.Overall);
            SetOverall(r.OverallKo);
            _sub.Text = $"rev {r.Revision}{(r.SameAsRevision is int s ? $" (=rev{s})" : "")} · {docName} · {DateTime.Now:HH:mm:ss}";
            _footer.Text = r.DisclaimerKo;

            _cards.SuspendLayout();
            _cards.Controls.Clear();
            var banner = Banner(r);
            if (banner != null) _cards.Controls.Add(banner);
            if (r.Readiness is { AllReady: false } rd) AddReadiness(rd);
            bool failOnly = UiState.Current.FailOnly;
            foreach (var x in r.Results)
            {
                if (failOnly && x.Status != "fail") continue;
                var card = new RuleCard(x, failOnly);
                card.FocusRequested += h => FocusRequested?.Invoke(h);
                card.HoverChanged += (h, on) => HoverRequested?.Invoke(h, on);
                card.FitRequested += rr => FitRequested?.Invoke(rr);
                card.ElementHover += (e, on) => ElementHoverRequested?.Invoke(e, on);
                card.ElementClicked += e => ElementFocusRequested?.Invoke(e);
                _cards.Controls.Add(card);
            }
            if (failOnly)
            {
                // 숨긴 항목 개수는 꼭 보여 준다 — '확인 필요'를 숨겨서 초록·파랑처럼 보이면 안 되므로 주황으로
                int pass = r.Results.Count(x => x.Status == "pass"), warn = r.Results.Count(x => x.Status is "na" or "review"),
                    skip = r.Results.Count(x => x.Status == "skip");
                var hidden = new List<string>();
                if (warn > 0) hidden.Add($"확인 필요 {warn}");
                if (pass > 0) hidden.Add($"기준 충족 {pass}");
                if (skip > 0) hidden.Add($"적용 제외 {skip}");
                if (hidden.Count > 0)
                    _cards.Controls.Add(Theme.Label("미충족만 보기 — 숨김: " + string.Join(" · ", hidden), Theme.Small, warn > 0 ? Theme.Warn : Theme.Muted));
                if (r.Results.All(x => x.Status != "fail"))
                    _cards.Controls.Add(warn > 0
                        ? Theme.Label($"미충족 규칙은 없지만 확인 필요 {warn}건을 숨겼습니다 — '미충족만 보기'를 끄면 보입니다.", Theme.Body, Theme.Warn)
                        : Theme.Label("미충족 규칙이 없습니다.", Theme.Body, Theme.Text));
            }
            _cards.ResumeLayout();
            FitCards();
            if (record) AddHistory(r);
        }

        /// 준비 상태 카드: 판정에 필요한 입력 중 빠진 것·잘못된 것을 한 번에(엔진 readiness). '모르면 멈춘다'의 이유를 보여 준다.
        private void AddReadiness(ReadinessDto rd)
        {
            var lines = new List<string>();
            foreach (var it in rd.Items)
            {
                if (it.Status is "missing" or "invalid")
                    lines.Add($"{(it.Status == "missing" ? "✖" : "⚠")} {it.NameKo} — {it.ProblemKo ?? it.HintKo ?? "없음"}");
                else if (it.Status == "default" && it.Required)
                    lines.Add($"· {it.NameKo}: {it.ValueKo} — {it.HintKo}");
            }
            foreach (var p in rd.ProblemsKo)
                if (!rd.Items.Any(i => i.ProblemKo == p)) lines.Add("⚠ " + p);
            var l = new Label
            {
                Text = $"준비 상태 {rd.Ready}/{rd.Total} — 판정에 필요한 입력\n" + string.Join("\n", lines),
                AutoSize = false, Font = Theme.Small, ForeColor = Theme.Text, BackColor = Theme.Card,
                Padding = new Padding(10, 6, 10, 6), Margin = new Padding(0, 0, 0, 2),
            };
            _cards.Controls.Add(l);
            var b = Theme.FlatButton("실도면 연결(GARCLINK) — 축척·영역·기준점·역할 지정", Theme.Info);
            b.Margin = new Padding(0, 0, 0, 8);
            b.Click += (_, _) => LinkRequested?.Invoke();
            _cards.Controls.Add(b);
        }

        private Control? Banner(CheckResponse r)
        {
            if (r.Transitions.Count == 0) return null;
            var lines = r.Transitions.Select(t =>
            {
                var title = r.Results.FirstOrDefault(x => x.RuleId == t.RuleId)?.TitleKo ?? t.RuleId;
                return t.Kind switch
                {
                    "new_violation" => $"새 위반 — {title}",
                    "resolved" => $"해결 — {title}",
                    "needs_human" => $"사람 확인 필요 — {title}",
                    _ => $"변경 — {title}",
                };
            });
            bool bad = r.Transitions.Any(t => t.Kind is "new_violation" or "needs_human");
            var l = new Label
            {
                Text = string.Join("\n", lines), AutoSize = false, Font = Theme.Bold,
                ForeColor = Color.White, BackColor = bad ? Theme.Fail : Theme.Pass,
                Padding = new Padding(10, 6, 10, 6), Margin = new Padding(0, 0, 0, 8),
            };
            return l;
        }

        private void AddHistory(CheckResponse r)
        {
            var d = r.Diff;
            var parts = new List<string>();
            if (d.Initial) parts.Add("최초 검사");
            if (!d.Initial && d.Added.Count > 0) parts.Add("추가 " + string.Join(",", d.Added));
            if (d.Removed.Count > 0) parts.Add("삭제 " + string.Join(",", d.Removed));
            if (d.Modified.Count > 0) parts.Add("수정 " + string.Join(",", d.Modified));
            if (!d.Initial && d.ShipChanged.Count > 0) parts.Add("선박정보");
            if (!d.Initial && d.ConditionsChanged) parts.Add("흘수조건");
            if (r.SameAsRevision is int s) parts.Add($"= rev{s} (Undo)");
            var trans = string.Join(", ", r.Transitions.Select(t => $"{Short(t.RuleId)} {t.From ?? "-"}→{t.To}"));
            var text = string.Join(" / ", parts) + (trans.Length > 0 ? "  ⇒ " + trans : "");
            var item = new ListViewItem(new[] { r.Revision.ToString(), DateTime.Now.ToString("HH:mm:ss"), text })
            {
                ForeColor = Theme.Status(r.Overall),
            };
            _history.Items.Insert(0, item);
            if (_history.Items.Count > 300) _history.Items.RemoveAt(_history.Items.Count - 1);
        }

        public void ClearHistory() => _history.Items.Clear();

        protected override void Dispose(bool disposing)
        {
            if (disposing) UiState.FailOnlyChanged -= OnFailOnlyChanged;   // 정적 이벤트가 패널을 붙잡지 않게
            base.Dispose(disposing);
        }

        private static string Short(string id) => id.StartsWith("SOLAS-V") ? "가시거리" : id.StartsWith("SOLAS-II1") ? "충돌격벽" : id;

        private void FitCards()
        {
            int w = Math.Max(_cards.ClientSize.Width - _cards.Padding.Horizontal - 4, 200);
            foreach (Control c in _cards.Controls)
            {
                c.Width = w;
                if (c is RuleCard rc) rc.Relayout();
                else if (c is Label l) { l.MaximumSize = new Size(w, 0); l.Height = l.GetPreferredSize(new Size(w, 0)).Height; }
            }
        }
    }

    public sealed class StatusDot : Control
    {
        private Color _color = Theme.Skip;
        [Browsable(false), DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public Color Color { get => _color; set { _color = value; Invalidate(); } }
        public StatusDot() { Size = new Size(14, 14); DoubleBuffered = true; }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            e.Graphics.Clear(Theme.EffectiveBack(Parent));
            using var b = new SolidBrush(_color);
            e.Graphics.FillEllipse(b, 1, 1, Width - 3, Height - 3);
        }
    }
}
