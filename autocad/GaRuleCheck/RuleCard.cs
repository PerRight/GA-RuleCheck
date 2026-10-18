// 규칙 하나의 결과 카드: 상태 띠·배지, 메시지, 현재값/허용값 게이지, 제안, 점검 항목(객체별 충족·미충족·확인 필요), 펼치기(근거·미검토).
// 점검 항목에 마우스를 올리면 도면에서 그 객체를 상태 색으로 강조하고, 누르면 그 객체로 확대·이동한다.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Linq;
using System.Text.Json;
using System.Windows.Forms;

namespace GaRuleCheck
{
    public sealed class RuleCard : Panel
    {
        public event Action<List<string>>? FocusRequested;
        /// 카드에 마우스를 올리면 도면에서 대상 객체를 임시 강조(true), 벗어나면 해제(false).
        public event Action<List<string>, bool>? HoverChanged;
        /// 시선·허용선 등 표시 전체가 화면에 들어오게 줌.
        public event Action<RuleResultDto>? FitRequested;
        /// 점검 항목(객체) hover / 클릭
        public event Action<ElementCheckDto, bool>? ElementHover;
        public event Action<ElementCheckDto>? ElementClicked;
        private readonly RuleResultDto _r;
        private bool _hover;
        private const int MaxRows = 40;   // 점검 항목 줄 수 상한(실도면은 객체가 수천 개)
        private Label? _more;
        private readonly Label _details;
        private bool _expanded;

        public RuleCard(RuleResultDto r, bool failOnly = false)
        {
            _r = r;
            DoubleBuffered = true;
            BackColor = Theme.Card;
            Padding = new Padding(14, 10, 10, 10);
            Margin = new Padding(0, 0, 0, 8);

            var flow = new FlowLayoutPanel
            {
                Dock = DockStyle.Top, FlowDirection = FlowDirection.TopDown, WrapContents = false,
                AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, BackColor = Color.Transparent,
            };
            Controls.Add(flow);

            // 1행: 제목 + 배지
            var head = new Panel { Height = 24, BackColor = Color.Transparent, Margin = Padding.Empty };
            var badge = new Badge(r.StatusKo, Theme.Status(r.Status)) { Dock = DockStyle.Right };
            var title = new Label
            {
                Text = r.TitleKo, Font = Theme.Bold, ForeColor = Theme.Text, Dock = DockStyle.Fill,
                AutoEllipsis = true, TextAlign = ContentAlignment.MiddleLeft, BackColor = Color.Transparent,
            };
            head.Controls.Add(title);
            head.Controls.Add(badge);
            flow.Controls.Add(head);
            flow.Controls.Add(Theme.Label(r.Ref + (r.Cached ? "  ·  캐시" : ""), Theme.Small, Theme.Muted));

            // 메시지
            flow.Controls.Add(Wrap(r.MessageKo, Theme.Body, Theme.Text, new Padding(0, 6, 0, 2)));

            // 게이지
            var g = Gauge.From(r);
            if (g != null) flow.Controls.Add(g);

            if (!string.IsNullOrEmpty(r.Condition))
                flow.Controls.Add(Theme.Label("최악 조건: " + r.Condition, Theme.Small, Theme.Muted));

            // 제안
            if (!string.IsNullOrEmpty(r.SuggestionKo))
            {
                var s = Wrap("제안  " + r.SuggestionKo, Theme.Body, Theme.Text, new Padding(0, 6, 0, 2));
                s.BackColor = Color.FromArgb(40, Theme.Info);
                s.Padding = new Padding(6, 4, 6, 4);
                flow.Controls.Add(s);
            }

            // 점검 항목(체크리스트): 미충족 → 확인 필요 → 충족 순.
            // 실도면은 블록을 펼친 하위 객체가 수천 개 → 블록 하나를 한 줄로(대표 = 가장 나쁜 하위 객체), 최대 MaxRows줄.
            var all = r.Elements.Where(e => !failOnly || e.Status == "fail").ToList();
            var rows = all.GroupBy(e => Extractor.BaseHandle(e.ElementId))
                .Select(g =>
                {
                    var worst = g.OrderBy(e => Theme.StatusRank(e.Status)).ThenBy(e => e.Margin ?? double.MaxValue).First();
                    if (g.Count() == 1) return worst;
                    int bad = g.Count(e => e.Status == "fail");
                    return new ElementCheckDto
                    {
                        ElementId = worst.ElementId, RuleId = worst.RuleId, RuleName = worst.RuleName, Role = worst.Role,
                        RoleKo = $"{worst.RoleKo} 블록 {g.Key} (하위 {g.Count()}개{(bad > 0 ? $", 미충족 {bad}" : "")})",
                        Status = worst.Status, Required = worst.Required, Actual = worst.Actual, Diff = worst.Diff,
                        Guide = worst.Guide, Margin = worst.Margin,
                    };
                })
                .OrderBy(e => Theme.StatusRank(e.Status)).ThenBy(e => e.RoleKo).ThenBy(e => e.ElementId).ToList();
            if (rows.Count > 0)
            {
                flow.Controls.Add(Theme.Label(failOnly ? "미충족 객체" : "점검 항목 (객체별)", Theme.Small, Theme.Muted));
                if (rows.Count > MaxRows)
                {
                    var rest = rows.Skip(MaxRows).ToList();
                    rows = rows.Take(MaxRows).ToList();
                    int f = rest.Count(e => e.Status == "fail"), u = rest.Count(e => e.Status == "unknown"), p = rest.Count - f - u;
                    _more = Theme.Label($"… 외 {rest.Count}줄 (미충족 {f} · 확인 필요 {u} · 충족 {p}) — 도면에서 객체에 커서를 올리면 판정이 보입니다",
                                        Theme.Small, f + u > 0 ? Theme.Warn : Theme.Muted);
                }
                foreach (var e in rows)
                {
                    var row = new ElementRow(e);
                    row.HoverChanged += on =>
                    {
                        // 객체 강조가 카드 전체 강조(자홍)와 겹치지 않게: 줄 위에서는 카드 강조를 잠시 끈다
                        if (_r.Targets.Count > 0 && _hover) HoverChanged?.Invoke(_r.Targets, !on);
                        ElementHover?.Invoke(e, on);
                    };
                    row.Clicked += () => ElementClicked?.Invoke(e);
                    flow.Controls.Add(row);
                }
                if (_more != null) flow.Controls.Add(_more);
            }

            // 버튼 줄
            var buttons = new FlowLayoutPanel { AutoSize = true, BackColor = Color.Transparent, Margin = new Padding(0, 6, 0, 0) };
            if (r.Targets.Count > 0)
            {
                var go = Theme.FlatButton("대상 보기");
                go.Click += (_, _) => FocusRequested?.Invoke(r.Targets);
                buttons.Controls.Add(go);
            }
            if (r.Overlays.Count > 0)
            {
                var fit = Theme.FlatButton("범위 보기");
                fit.Click += (_, _) => FitRequested?.Invoke(r);
                buttons.Controls.Add(fit);
            }
            var more = Theme.FlatButton("근거 ▾");
            buttons.Controls.Add(more);
            flow.Controls.Add(buttons);

            _details = Wrap(DetailsText(r), Theme.Small, Theme.Muted, new Padding(0, 6, 0, 0));
            _details.Visible = false;
            flow.Controls.Add(_details);
            more.Click += (_, _) =>
            {
                _expanded = !_expanded;
                _details.Visible = _expanded;
                more.Text = _expanded ? "근거 ▴" : "근거 ▾";
                Relayout();
            };
            Resize += (_, _) => Relayout();
            HookHover(this);
        }

        private void HookHover(Control c)
        {
            if (c is ElementRow) return;   // 점검 항목 줄은 자기 hover(객체 강조)를 따로 낸다
            c.MouseEnter += (_, _) => SetHover(true);
            c.MouseLeave += (_, _) =>
            {
                if (!IsDisposed && !ClientRectangle.Contains(PointToClient(Cursor.Position))) SetHover(false);
            };
            foreach (Control child in c.Controls) HookHover(child);
        }

        private void SetHover(bool on)
        {
            if (_hover == on) return;
            _hover = on;
            BackColor = on ? Theme.CardHover : Theme.Card;
            Invalidate(true);
            if (_r.Targets.Count > 0) HoverChanged?.Invoke(_r.Targets, on);
        }

        public void Relayout()
        {
            int inner = Math.Max(Width - Padding.Horizontal - 4, 120);
            foreach (Control c in Controls[0].Controls)
            {
                if (c is Label l && !l.AutoSize) { l.MaximumSize = new Size(inner, 0); l.Width = inner; l.Height = l.GetPreferredSize(new Size(inner, 0)).Height + l.Padding.Vertical; }
                if (c is Gauge gg) gg.Width = inner;
                if (c is ElementRow er) { er.Width = inner; er.Relayout(); }
                if (c is Panel p && c is not FlowLayoutPanel) p.Width = inner;
            }
            if (Controls[0] is FlowLayoutPanel f) Height = f.PreferredSize.Height + Padding.Vertical;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using var b = new SolidBrush(Theme.Status(_r.Status));
            e.Graphics.FillRectangle(b, 0, 0, 4, Height);
        }

        private static Label Wrap(string text, Font font, Color color, Padding margin) => new()
        {
            Text = text, AutoSize = false, Font = font, ForeColor = color, Margin = margin, BackColor = Color.Transparent,
        };

        private static string DetailsText(RuleResultDto r)
        {
            var lines = new List<string>();
            if (r.Targets.Count > 0) lines.Add("대상 객체: " + string.Join(", ", r.Targets));
            if (r.Limitations.Count > 0)
            {
                lines.Add("이 판정이 다루지 않는 것:");
                lines.AddRange(r.Limitations.Select(x => "  · " + x));
            }
            lines.Add("수식·중간값 전체는 '리포트'에서 확인");
            return string.Join(Environment.NewLine, lines);
        }
    }

    /// 점검 항목 한 줄: 상태 아이콘(충족 파랑 ✔ / 미충족 빨강 ✖ / 확인 필요 주황 ⚠) + 객체 + 기준·실제·차이·가이드.
    public sealed class ElementRow : Control
    {
        public ElementCheckDto Element { get; }
        public event Action<bool>? HoverChanged;
        public event Action? Clicked;
        private bool _hover;

        public ElementRow(ElementCheckDto e)
        {
            Element = e;
            DoubleBuffered = true;
            Cursor = Cursors.Hand;
            Margin = new Padding(0, 2, 0, 2);
            AccessibleRole = AccessibleRole.ListItem;
            AccessibleName = $"{e.RoleKo} {e.ElementId} {Theme.ElementMark(e.Status)}";
            AccessibleDescription = Detail();
            Height = 40;
        }

        private string Title() => $"{Theme.ElementMark(Element.Status)}  ·  {Element.RoleKo} {Element.ElementId}";

        private string Detail()
        {
            var t = $"기준 {Element.Required}\n실제 {Element.Actual}";
            if (!string.IsNullOrEmpty(Element.Diff)) t += $"\n차이 {Element.Diff}";
            if (!string.IsNullOrEmpty(Element.Guide)) t += $"\n{(Element.Status == "unknown" ? "사유" : "가이드")} {Element.Guide}";
            return t;
        }

        public void Relayout()
        {
            int tw = Math.Max(Width - 30, 80);
            int h1 = TextRenderer.MeasureText(Title(), Theme.Bold, new Size(tw, 0), TextFormatFlags.WordBreak).Height;
            int h2 = TextRenderer.MeasureText(Detail(), Theme.Small, new Size(tw, 0), TextFormatFlags.WordBreak).Height;
            Height = h1 + h2 + 10;
            Invalidate();
        }

        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); HoverChanged?.Invoke(true); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); HoverChanged?.Invoke(false); base.OnMouseLeave(e); }
        protected override void OnClick(EventArgs e) { Clicked?.Invoke(); base.OnClick(e); }

        protected override void OnPaint(PaintEventArgs pe)
        {
            var g = pe.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(_hover ? Theme.CardHover : Theme.EffectiveBack(Parent));
            var col = Theme.Status(Element.Status);
            DrawIcon(g, Element.Status, new RectangleF(2, 4, 16, 16), col);
            int tw = Math.Max(Width - 30, 80);
            int h1 = TextRenderer.MeasureText(Title(), Theme.Bold, new Size(tw, 0), TextFormatFlags.WordBreak).Height;
            TextRenderer.DrawText(g, Title(), Theme.Bold, new Rectangle(24, 2, tw, h1), col, TextFormatFlags.WordBreak);
            TextRenderer.DrawText(g, Detail(), Theme.Small, new Rectangle(24, 4 + h1, tw, Height - h1 - 4), Theme.Muted, TextFormatFlags.WordBreak);
        }

        /// 글꼴에 기대지 않는 상태 아이콘: 충족 ○✔, 미충족 ○✖, 확인 필요 △!
        public static void DrawIcon(Graphics g, string status, RectangleF r, Color col)
        {
            using var b = new SolidBrush(col);
            using var w = new Pen(Color.White, 1.8f) { StartCap = LineCap.Round, EndCap = LineCap.Round };
            if (status == "pass" || status == "fail")
            {
                g.FillEllipse(b, r);
                if (status == "pass")
                    g.DrawLines(w, new[] { new PointF(r.X + r.Width * 0.27f, r.Y + r.Height * 0.52f), new PointF(r.X + r.Width * 0.44f, r.Y + r.Height * 0.70f), new PointF(r.X + r.Width * 0.74f, r.Y + r.Height * 0.32f) });
                else
                {
                    g.DrawLine(w, r.X + r.Width * 0.32f, r.Y + r.Height * 0.32f, r.X + r.Width * 0.68f, r.Y + r.Height * 0.68f);
                    g.DrawLine(w, r.X + r.Width * 0.68f, r.Y + r.Height * 0.32f, r.X + r.Width * 0.32f, r.Y + r.Height * 0.68f);
                }
                return;
            }
            g.FillPolygon(b, new[] { new PointF(r.X + r.Width / 2, r.Y), new PointF(r.Right, r.Bottom), new PointF(r.X, r.Bottom) });
            g.DrawLine(w, r.X + r.Width / 2, r.Y + r.Height * 0.35f, r.X + r.Width / 2, r.Y + r.Height * 0.65f);
            g.DrawLine(w, r.X + r.Width / 2, r.Y + r.Height * 0.82f, r.X + r.Width / 2, r.Y + r.Height * 0.84f);
        }
    }

    /// 둥근 상태 배지.
    public sealed class Badge : Control
    {
        private readonly Color _color;
        public Badge(string text, Color color)
        {
            Text = text;
            _color = color;
            Font = Theme.Small;
            DoubleBuffered = true;
            var sz = TextRenderer.MeasureText(text, Font);
            Size = new Size(sz.Width + 14, sz.Height + 4);
            Margin = new Padding(6, 0, 0, 0);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Theme.EffectiveBack(Parent));
            using var path = Round(new Rectangle(0, 0, Width - 1, Height - 1), Height / 2);
            using var b = new SolidBrush(_color);
            g.FillPath(b, path);
            TextRenderer.DrawText(g, Text, Font, ClientRectangle, Color.White,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }

        internal static GraphicsPath Round(Rectangle r, int radius)
        {
            var p = new GraphicsPath();
            int d = Math.Max(radius * 2, 1);
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }
    }

    /// 현재값과 허용값을 한 줄 막대로. 상한형(≤ limit)과 구간형([lo, hi]) 두 가지.
    public sealed class Gauge : Control
    {
        private double? _actualLo, _actualHi, _lo;
        private double _hi, _max;
        private bool _infinite;
        private string _status = "";

        public static Gauge? From(RuleResultDto r)
        {
            if (r.Status is "skip" or "na" or "review") return null;
            var g = new Gauge { _status = r.Status, Height = 34, Margin = new Padding(0, 6, 0, 2) };
            var lim = Nums(r.Limit);
            var act = Nums(r.Actual);
            if (lim.Count == 1) { g._hi = lim[0]; }
            else if (lim.Count == 2) { g._lo = lim[0]; g._hi = lim[1]; }
            else return null;
            if (act.Count == 0) g._infinite = true;
            else { g._actualLo = act.Min(); g._actualHi = act.Max(); }
            g._max = Math.Max(g._hi, g._actualHi ?? g._hi) * 1.15;
            if (g._max <= 0) return null;
            return g;
        }

        private static List<double> Nums(JsonElement? v)
        {
            var list = new List<double>();
            if (v is not JsonElement e) return list;
            if (e.ValueKind == JsonValueKind.Number) list.Add(e.GetDouble());
            else if (e.ValueKind == JsonValueKind.Array)
                list.AddRange(e.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.Number).Select(x => x.GetDouble()));
            return list;
        }

        public Gauge()
        {
            DoubleBuffered = true;
            Font = Theme.Small;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Theme.EffectiveBack(Parent));
            int w = Width - 2, barY = 4, barH = 10;
            float X(double v) => (float)(1 + Math.Clamp(v / _max, 0, 1) * (w - 1));

            using (var track = new SolidBrush(Theme.Line)) g.FillRectangle(track, 1, barY, w, barH);
            using (var ok = new SolidBrush(Color.FromArgb(90, Theme.Pass)))
            {
                float a = X(_lo ?? 0), b = X(_hi);
                g.FillRectangle(ok, a, barY, b - a, barH);
            }
            var col = Theme.Status(_status);
            if (_infinite)
            {
                using var f = new SolidBrush(col);
                g.FillRectangle(f, X(_hi), barY, w - X(_hi), barH);
            }
            else if (_actualLo is double lo && _actualHi is double hi)
            {
                using var f = new SolidBrush(col);
                if (_lo == null) g.FillRectangle(f, 1, barY + 3, X(hi) - 1, barH - 6);
                float a = X(lo), b = X(hi);
                g.FillRectangle(f, a - 1.5f, barY - 3, Math.Max(b - a, 0) + 3, barH + 6);
            }
            using var pen = new Pen(Theme.Text, 1.5f);
            if (_lo is double l) g.DrawLine(pen, X(l), barY - 2, X(l), barY + barH + 2);
            g.DrawLine(pen, X(_hi), barY - 2, X(_hi), barY + barH + 2);

            string cur = _infinite ? "현재 ∞" : (_actualLo == _actualHi ? $"현재 {_actualHi:0.##} m" : $"현재 {_actualLo:0.##}~{_actualHi:0.##} m");
            string lim = _lo is double l2 ? $"허용 {l2:0.##}~{_hi:0.##} m" : $"허용 ≤ {_hi:0.##} m";
            TextRenderer.DrawText(g, cur, Font, new Point(0, barY + barH + 3), col);
            var sz = TextRenderer.MeasureText(lim, Font);
            TextRenderer.DrawText(g, lim, Font, new Point(Width - sz.Width, barY + barH + 3), Theme.Muted);
        }
    }
}
