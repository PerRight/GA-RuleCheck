// 버전 비교 그림: 엔진 /v1/compare가 준 두 버전 형상(m 좌표)을 그린다. 판단·diff 계산은 하지 않는다.
//   Before/After : 나란히 보기의 왼쪽(이전)·오른쪽(이후) — 두 칸이 같은 ViewState를 공유해 같이 확대·이동
//   Overlay      : 이전 위에 이후를 슬라이더 투명도로 겹쳐 보기
//   Diff         : 차이만 강조 — 추가 초록(빗금), 삭제 빨강(파선), 변경 노랑(이전 점선 + 이후 실선)
// 흑백 인쇄에서도 구분되게 색과 함께 선 모양·빗금·표식 모양(추가 ○, 삭제 □, 변경 ◇)과 기호(+ − ~)를 쓴다.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Linq;
using System.Windows.Forms;

namespace GaRuleCheck
{
    public enum CanvasMode { Before, After, Overlay, Diff }

    /// 화면 보기 상태(중심 m, 배율 px/m). 나란히 보기의 두 칸이 하나를 공유한다.
    public sealed class ViewState
    {
        public double Cx, Cy, Scale = 1;
        public event Action? Changed;
        public void Notify() => Changed?.Invoke();

        public void Fit(double[] box, Size size, double margin = 0.08)
        {
            if (box.Length < 4 || size.Width <= 0 || size.Height <= 0) return;
            double w = Math.Max(box[2] - box[0], 1e-6), h = Math.Max(box[3] - box[1], 1e-6);
            Cx = (box[0] + box[2]) / 2;
            Cy = (box[1] + box[3]) / 2;
            Scale = Math.Min(size.Width * (1 - 2 * margin) / w, size.Height * (1 - 2 * margin) / h);
            Notify();
        }
    }

    public sealed class PaintStyle
    {
        public bool Print;
        public Color Back, Base, Text, Faint;
        public float Px = 1f;   // 선 두께 배율(인쇄 해상도)

        public static PaintStyle Screen => new()
        {
            Back = Color.FromArgb(24, 27, 32), Base = Color.FromArgb(150, 160, 172), Text = Theme.Text,
            Faint = Color.FromArgb(70, 78, 90),
        };

        public static PaintStyle Paper(float px) => new()
        {
            Print = true, Back = Color.White, Base = Color.FromArgb(80, 80, 80), Text = Color.Black,
            Faint = Color.FromArgb(185, 185, 185), Px = px,
        };
    }

    public static class CanvasPainter
    {
        public static Color KindColor(string kind) => kind switch
        {
            "added" => Theme.DiffAdded,
            "removed" => Theme.DiffRemoved,
            _ => Theme.DiffModified,
        };

        public static string KindGlyph(string kind) => kind switch { "added" => "+", "removed" => "−", _ => "~" };
        public static string KindKo(string kind) => kind switch { "added" => "추가", "removed" => "삭제", "modified" => "변경", "ship" => "선박 정보", _ => "흘수 조건" };

        public static PointF ToScreen(ViewState v, RectangleF area, double x, double y) =>
            new((float)(area.X + area.Width / 2 + (x - v.Cx) * v.Scale), (float)(area.Y + area.Height / 2 - (y - v.Cy) * v.Scale));

        public static (double x, double y) ToModel(ViewState v, RectangleF area, PointF p) =>
            (v.Cx + (p.X - area.X - area.Width / 2) / v.Scale, v.Cy - (p.Y - area.Y - area.Height / 2) / v.Scale);

        public static void Paint(Graphics g, RectangleF area, CompareResponse d, CanvasMode mode, ViewState v,
                                 PaintStyle st, float overlayOpacity, int? highlight, IList<(ChangeDto c, RectangleF r)>? markersOut = null)
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (var bg = new SolidBrush(st.Back)) g.FillRectangle(bg, area);
            var clip = g.Clip;
            g.SetClip(area);
            var changed = d.Changes.SelectMany(c => c.Handles.Select(h => (h, c))).GroupBy(x => x.h).ToDictionary(x => x.Key, x => x.First().c);
            var a = d.Geometry.A.ToDictionary(x => x.Handle);
            var b = d.Geometry.B.ToDictionary(x => x.Handle);

            switch (mode)
            {
                case CanvasMode.Before:
                case CanvasMode.After:
                {
                    bool before = mode == CanvasMode.Before;
                    var geo = before ? d.Geometry.A : d.Geometry.B;
                    foreach (var gm in geo)
                    {
                        if (changed.TryGetValue(gm.Handle, out var c) && (before ? c.Kind != "added" : c.Kind != "removed"))
                            DrawChanged(g, area, v, st, gm, c.Kind, before ? "before" : "after", c.Number == highlight);
                        else
                            DrawBase(g, area, v, st, gm, st.Base);
                    }
                    break;
                }
                case CanvasMode.Overlay:
                {
                    var colA = st.Print ? Color.FromArgb(60, 60, 60) : Color.FromArgb(170, 178, 190);
                    var colB = Color.FromArgb((int)Math.Round(255 * Math.Clamp(overlayOpacity, 0f, 1f)), st.Print ? Color.FromArgb(0, 110, 200) : Color.FromArgb(80, 200, 255));
                    foreach (var gm in d.Geometry.A) DrawBase(g, area, v, st, gm, colA);
                    foreach (var gm in d.Geometry.B) DrawBase(g, area, v, st, gm, colB, 1.6f);
                    break;
                }
                default:   // Diff
                {
                    foreach (var gm in d.Geometry.B)
                        if (!changed.ContainsKey(gm.Handle)) DrawBase(g, area, v, st, gm, st.Faint);
                    foreach (var c in d.Changes)
                    {
                        bool hl = c.Number == highlight;
                        foreach (var h in c.Handles)   // 묶인 블록이면 하위 객체 전부
                        {
                            if (c.Kind == "added" && b.TryGetValue(h, out var gb)) DrawChanged(g, area, v, st, gb, "added", "after", hl);
                            else if (c.Kind == "removed" && a.TryGetValue(h, out var ga)) DrawChanged(g, area, v, st, ga, "removed", "before", hl);
                            else if (c.Kind == "modified")
                            {
                                if (a.TryGetValue(h, out var g0)) DrawChanged(g, area, v, st, g0, "modified", "before", hl);
                                if (b.TryGetValue(h, out var g1)) DrawChanged(g, area, v, st, g1, "modified", "after", hl);
                            }
                        }
                    }
                    break;
                }
            }

            // 번호 표식: 겹치면 위로 비켜 놓고 가는 지시선으로 위치를 잇는다
            var placed = new List<RectangleF>();
            float r = 11 * st.Px;
            using var font = new Font(Theme.FontName, 8.5f * st.Px, FontStyle.Bold, GraphicsUnit.Pixel);
            foreach (var c in d.Changes.Where(x => x.Bbox != null && x.Center != null))
            {
                if (mode == CanvasMode.Before && c.Kind == "added") continue;
                if (mode == CanvasMode.After && c.Kind == "removed") continue;
                var top = ToScreen(v, area, c.Center![0], c.Bbox![3]);
                var anchor = ToScreen(v, area, c.Center[0], c.Center[1]);
                var rect = new RectangleF(top.X - r, top.Y - 2 * r - 8 * st.Px, 2 * r, 2 * r);
                for (int guard = 0; guard < 20 && placed.Any(p => p.IntersectsWith(RectangleF.Inflate(rect, 2, 2))); guard++)
                    rect.Y -= 2 * r + 2;
                placed.Add(rect);
                markersOut?.Add((c, rect));
                DrawMarker(g, st, c, rect, anchor, font, c.Number == highlight);
            }
            g.Clip = clip;
        }

        private static PointF[] Pts(GeomDto gm, RectangleF area, ViewState v)
        {
            var pts = gm.Points.Select(p => ToScreen(v, area, p[0], p[1])).ToList();
            if (gm.Closed && pts.Count > 2) pts.Add(pts[0]);
            return pts.ToArray();
        }

        private static void DrawBase(Graphics g, RectangleF area, ViewState v, PaintStyle st, GeomDto gm, Color col, float width = 1f)
        {
            var pts = Pts(gm, area, v);
            using var pen = new Pen(col, width * st.Px);
            if (pts.Length == 1) { float s = 3 * st.Px; g.DrawEllipse(pen, pts[0].X - s, pts[0].Y - s, 2 * s, 2 * s); return; }
            if (pts.Length >= 2) g.DrawLines(pen, pts);
        }

        /// stage: before(이전 형상) / after(이후 형상). 변경은 이전을 점선, 이후를 실선으로.
        private static void DrawChanged(Graphics g, RectangleF area, ViewState v, PaintStyle st, GeomDto gm, string kind, string stage, bool hl)
        {
            var pts = Pts(gm, area, v);
            var col = KindColor(kind);
            float w = (hl ? 4f : 2.6f) * st.Px;
            using var pen = new Pen(col, w);
            if (kind == "removed") pen.DashStyle = DashStyle.Dash;
            else if (kind == "modified" && stage == "before") { pen.DashStyle = DashStyle.Dot; pen.Width = 1.8f * st.Px; pen.Color = Color.FromArgb(200, col); }
            bool closed = pts.Length > 3 && pts[0] == pts[^1];
            if (closed && (kind == "added" || kind == "removed" || stage == "after"))
            {
                var hatch = kind switch { "added" => HatchStyle.ForwardDiagonal, "removed" => HatchStyle.DiagonalCross, _ => HatchStyle.Percent20 };
                using var hb = new HatchBrush(hatch, Color.FromArgb(st.Print ? 200 : 150, col), Color.Transparent);
                g.FillPolygon(hb, pts);
            }
            if (hl)
            {
                using var glow = new Pen(Color.FromArgb(90, col), w + 8 * st.Px) { LineJoin = LineJoin.Round };
                if (pts.Length >= 2) g.DrawLines(glow, pts);
            }
            if (pts.Length == 1) { float s = 5 * st.Px; g.DrawEllipse(pen, pts[0].X - s, pts[0].Y - s, 2 * s, 2 * s); return; }
            if (pts.Length >= 2) g.DrawLines(pen, pts);
        }

        public static void DrawMarker(Graphics g, PaintStyle st, ChangeDto c, RectangleF rect, PointF? anchor, Font font, bool hl)
        {
            var col = KindColor(c.Kind);
            if (anchor is PointF an)
                using (var lead = new Pen(Color.FromArgb(160, col), 1f * st.Px)) g.DrawLine(lead, rect.X + rect.Width / 2, rect.Bottom, an.X, an.Y);
            using var path = MarkerPath(c.Kind, rect);
            using (var fill = new SolidBrush(st.Print ? Color.White : Color.FromArgb(30, 34, 40))) g.FillPath(fill, path);
            using (var pen = new Pen(col, (hl ? 3f : 2f) * st.Px)) g.DrawPath(pen, path);
            var txt = c.Number.ToString();
            var tc = st.Print ? Color.Black : Color.White;
            var sz = g.MeasureString(txt, font);
            using (var tb = new SolidBrush(tc)) g.DrawString(txt, font, tb, rect.X + (rect.Width - sz.Width) / 2, rect.Y + (rect.Height - sz.Height) / 2);
            // 흑백에서도 종류가 보이게 기호를 표식 오른쪽 위에
            using var gf = new Font(Theme.FontName, 8f * st.Px, FontStyle.Bold, GraphicsUnit.Pixel);
            using var gb = new SolidBrush(st.Print ? Color.Black : col);
            g.DrawString(KindGlyph(c.Kind), gf, gb, rect.Right - 2 * st.Px, rect.Y - 6 * st.Px);
        }

        public static GraphicsPath MarkerPath(string kind, RectangleF r)
        {
            var p = new GraphicsPath();
            switch (kind)
            {
                case "removed": p.AddRectangle(r); break;
                case "modified":
                    p.AddPolygon(new[] { new PointF(r.X + r.Width / 2, r.Y - 2), new PointF(r.Right + 2, r.Y + r.Height / 2),
                                         new PointF(r.X + r.Width / 2, r.Bottom + 2), new PointF(r.X - 2, r.Y + r.Height / 2) });
                    break;
                default: p.AddEllipse(r); break;
            }
            return p;
        }

        /// 범례(화면 하단·내보내기 공용): 색 + 선 모양 + 표식 모양 + 기호
        public static void PaintLegend(Graphics g, RectangleF area, PaintStyle st)
        {
            using var font = new Font(Theme.FontName, 9f * st.Px, GraphicsUnit.Pixel);
            float x = area.X, y = area.Y + area.Height / 2;
            foreach (var kind in new[] { "added", "removed", "modified" })
            {
                var col = KindColor(kind);
                using (var pen = new Pen(col, 2.6f * st.Px) { DashStyle = kind == "removed" ? DashStyle.Dash : DashStyle.Solid })
                    g.DrawLine(pen, x, y, x + 26 * st.Px, y);
                if (kind == "modified")
                    using (var dot = new Pen(Color.FromArgb(200, col), 1.8f * st.Px) { DashStyle = DashStyle.Dot })
                        g.DrawLine(dot, x, y + 5 * st.Px, x + 26 * st.Px, y + 5 * st.Px);
                x += 30 * st.Px;
                var mr = new RectangleF(x, y - 8 * st.Px, 16 * st.Px, 16 * st.Px);
                using (var path = MarkerPath(kind, mr))
                using (var pen = new Pen(col, 1.8f * st.Px)) g.DrawPath(pen, path);
                x += 22 * st.Px;
                var label = kind switch
                {
                    "added" => "+ 추가 (초록·빗금·○)",
                    "removed" => "− 삭제 (빨강·파선·□)",
                    _ => "~ 변경 (노랑·이전 점선/이후 실선·◇)",
                };
                using var tb = new SolidBrush(st.Text);
                g.DrawString(label, font, tb, x, y - font.Height / 2f);
                x += g.MeasureString(label, font).Width + 18 * st.Px;
            }
        }
    }

    /// 화면용 비교 캔버스. 휠 확대, 드래그 이동, 더블클릭 전체 보기, 표식 클릭·hover.
    public sealed class CompareCanvas : Control
    {
        public CompareResponse? Data { get; private set; }
        public CanvasMode Mode { get; set; } = CanvasMode.Diff;
        public float OverlayOpacity { get; set; } = 0.6f;
        public int? Highlight { get; set; }
        public ViewState View { get; private set; } = new();
        public string Caption { get; set; } = "";

        public event Action<ChangeDto>? MarkerClicked;
        public event Action<ChangeDto?>? ChangeHovered;

        private readonly List<(ChangeDto c, RectangleF r)> _markers = new();
        private HitIndex? _index;
        private Dictionary<string, ChangeDto> _byHandle = new();
        private Point _down;
        private bool _dragging;
        private (double cx, double cy) _downCenter;
        private readonly Stopwatch _throttle = Stopwatch.StartNew();
        private readonly ToolTip _tip = new() { InitialDelay = 150, ReshowDelay = 50, AutoPopDelay = 15000 };
        private ChangeDto? _hover;

        public CompareCanvas()
        {
            DoubleBuffered = true;
            SetStyle(ControlStyles.ResizeRedraw | ControlStyles.Selectable, true);
            BackColor = PaintStyle.Screen.Back;
        }

        /// data가 null이면(비교 실패) 이전 그림을 지우고 안내 문구만 보인다 — 이전 결과가 남아 보이지 않게.
        public void Bind(CompareResponse? data, ViewState shared)
        {
            View.Changed -= Invalidate;
            View = shared;
            View.Changed += Invalidate;
            Data = data;
            _markers.Clear();
            _hover = null;
            _tip.SetToolTip(this, "");
            if (data == null) { _byHandle = new(); _index = null; Invalidate(); return; }
            _byHandle = data.Changes.SelectMany(c => c.Handles.Select(h => (h, c))).GroupBy(x => x.h).ToDictionary(g => g.Key, g => g.First().c);
            var shapes = data.Geometry.A.Concat(data.Geometry.B)
                .Where(gm => _byHandle.ContainsKey(gm.Handle))
                .Select(gm => (gm.Handle, (IReadOnlyList<double[]>)gm.Points, gm.Closed));
            _index = new HitIndex(shapes);
            Invalidate();
        }

        public void FitAll()
        {
            if (Data?.Bounds is double[] b) View.Fit(b, ClientSize);
        }

        public void FocusChange(ChangeDto c)
        {
            if (c.Bbox is not double[] b) return;
            double w = Math.Max(b[2] - b[0], 1), h = Math.Max(b[3] - b[1], 1);
            View.Fit(new[] { b[0] - w * 1.5, b[1] - h * 1.5, b[2] + w * 1.5, b[3] + h * 1.5 }, ClientSize);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var st = PaintStyle.Screen;
            if (Data == null)
            {
                e.Graphics.Clear(st.Back);
                TextRenderer.DrawText(e.Graphics, "비교할 버전을 고르세요", Theme.Body, ClientRectangle, Theme.Muted,
                                      TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
                return;
            }
            _markers.Clear();
            try
            {
                CanvasPainter.Paint(e.Graphics, ClientRectangle, Data, Mode, View, st, OverlayOpacity, Highlight, _markers);
            }
            catch (Exception ex)   // 극단 확대 등으로 GDI+ 오버플로가 나도 창이 죽지 않게
            {
                e.Graphics.Clear(st.Back);
                TextRenderer.DrawText(e.Graphics, "그림을 그리지 못했습니다 — 두 번 클릭하면 전체 보기 (" + ex.GetType().Name + ")", Theme.Body,
                                      ClientRectangle, Theme.Warn, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
            }
            if (!string.IsNullOrEmpty(Caption))
            {
                var sz = TextRenderer.MeasureText(Caption, Theme.Bold);
                using var bb = new SolidBrush(Color.FromArgb(200, Theme.Card));
                e.Graphics.FillRectangle(bb, 8, 8, sz.Width + 12, sz.Height + 6);
                TextRenderer.DrawText(e.Graphics, Caption, Theme.Bold, new Point(14, 11), Theme.Text);
            }
        }

        protected override void OnMouseWheel(MouseEventArgs e)
        {
            if (Data == null) return;
            var (mx, my) = CanvasPainter.ToModel(View, ClientRectangle, e.Location);
            double f = e.Delta > 0 ? 1.2 : 1 / 1.2;
            // 확대 범위 제한: 전체 보기의 1/20 ~ 2000배 (GDI+ 좌표 넘침 방지)
            if (Data.Bounds is double[] b && b.Length >= 4 && ClientSize.Width > 0 && ClientSize.Height > 0)
            {
                double fit = Math.Min(ClientSize.Width / Math.Max(b[2] - b[0], 1e-6), ClientSize.Height / Math.Max(b[3] - b[1], 1e-6));
                double next = View.Scale * f;
                if (next < fit * 0.05 || next > fit * 2000 || double.IsNaN(next)) return;
            }
            View.Scale *= f;
            View.Cx = mx - (mx - View.Cx) / f;
            View.Cy = my - (my - View.Cy) / f;
            View.Notify();
        }

        /// 마우스가 올라오면 포커스를 받아 휠 확대가 바로 먹게 한다(창이 활성일 때만).
        protected override void OnMouseEnter(EventArgs e)
        {
            base.OnMouseEnter(e);
            if (FindForm() is Form f && f == Form.ActiveForm && !Focused) Focus();
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            Focus();
            _down = e.Location;
            _downCenter = (View.Cx, View.Cy);
            _dragging = false;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            if (Data == null) return;
            if (e.Button is MouseButtons.Left or MouseButtons.Middle)
            {
                int dx = e.X - _down.X, dy = e.Y - _down.Y;
                if (_dragging || Math.Abs(dx) + Math.Abs(dy) > 4)
                {
                    _dragging = true;
                    View.Cx = _downCenter.cx - dx / View.Scale;
                    View.Cy = _downCenter.cy + dy / View.Scale;
                    View.Notify();
                }
                return;
            }
            if (_throttle.ElapsedMilliseconds < 30) return;   // hover는 30 ms에 한 번만
            _throttle.Restart();
            var hit = HitAt(e.Location);
            if (hit != _hover)
            {
                _hover = hit;
                ChangeHovered?.Invoke(hit);
                _tip.SetToolTip(this, hit == null ? "" : $"{hit.Number}. {hit.DescKo}\n이전: {hit.BeforeKo}\n이후: {hit.AfterKo}"
                    + string.Concat(hit.Resolved.Select(x => "\n✔ " + x.Text)) + string.Concat(hit.Introduced.Select(x => "\n✖ " + x.Text))
                    + string.Concat(hit.NeedsCheck.Select(x => "\n⚠ " + x.Text)));
            }
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            if (Data == null || _dragging) { _dragging = false; return; }
            var hit = HitAt(e.Location);
            if (hit != null) MarkerClicked?.Invoke(hit);
        }

        protected override void OnDoubleClick(EventArgs e) => FitAll();

        private ChangeDto? HitAt(Point p)
        {
            foreach (var (c, r) in _markers)
                if (RectangleF.Inflate(r, 3, 3).Contains(p)) return c;
            if (_index == null) return null;
            var (x, y) = CanvasPainter.ToModel(View, ClientRectangle, p);
            var h = _index.Hit(x, y, 6 / View.Scale);
            return h != null && _byHandle.TryGetValue(h, out var c2) ? c2 : null;
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) { View.Changed -= Invalidate; _tip.Dispose(); }
            base.Dispose(disposing);
        }
    }
}
