// 리본 'GA RuleCheck' 탭. 버튼은 명령 문자열을 실행만 한다(로직은 Commands에).
// 아이콘은 이미지 파일 없이 코드로 그린다(색 사각형 + 글자).
using System;
using System.Globalization;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Autodesk.Windows;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;

namespace GaRuleCheck
{
    public static class RibbonUi
    {
        private const string TabId = "GARC_RIBBON_TAB";

        /// 리본이 아직 없으면(시작 직후) 준비되는 시점에 만든다.
        public static void Install()
        {
            if (ComponentManager.Ribbon != null) { Create(); return; }
            ComponentManager.ItemInitialized += OnItemInitialized;
        }

        private static void OnItemInitialized(object? sender, RibbonItemEventArgs e)
        {
            if (ComponentManager.Ribbon == null) return;
            ComponentManager.ItemInitialized -= OnItemInitialized;
            Create();
        }

        public static void Create()
        {
            var ribbon = ComponentManager.Ribbon;
            if (ribbon == null) return;
            foreach (var t in ribbon.Tabs)
                if (t.Id == TabId) return;

            var tab = new RibbonTab { Title = "GA RuleCheck", Id = TabId };
            ribbon.Tabs.Add(tab);

            tab.Panels.Add(Panel("검토",
                Big("검사 시작", "GARC", "▶", Color.FromRgb(59, 130, 220), "팔레트를 열고 이 도면을 감시합니다. GA_ 레이어가 바뀌면 자동 재검사."),
                Big("지금 검사", "GARCCHECK", "⟳", Color.FromRgb(70, 150, 120), "즉시 다시 검사"),
                Big("감시 중지", "GARCOFF", "■", Color.FromRgb(120, 128, 140), "자동 검사를 멈추고 표시를 지웁니다.")));
            tab.Panels.Add(Panel("실도면",
                Big("실도면 연결", "GARCLINK", "⚓", Color.FromRgb(30, 140, 160),
                    "GA_ 레이어가 없는 실제 도면을 검사할 수 있게: 치수표 읽기·축척 → 측면도 영역 → AP/FP/기선 → 선체·장애물·충돌격벽 역할 지정 → 첫 검사. 레이어는 바꾸지 않습니다."),
                Big("역할 지정", "GARCROLE", "≡", Color.FromRgb(90, 140, 170), "고른 객체에 선체·장애물·충돌격벽·구상선수 역할을 붙이거나 해제(레이어는 그대로)"),
                Big("역할 보기", "GARCROLESHOW", "◫", Color.FromRgb(120, 150, 170), "엔진이 판정에 쓴 객체를 역할별 색으로 잠시 표시(도면은 그대로)")));
            tab.Panels.Add(Panel("입력",
                Big("선박 정보", "GARCSHIP", "i", Color.FromRgb(95, 110, 200), "LOA·L·AP·FP·흘수 조건 편집"),
                Big("조종위치", "GARCEYE", "◉", Color.FromRgb(60, 160, 90), "GA_EYE 점(선교 갑판 높이)을 찍습니다."),
                Big("초기 설정", "GARCSETUP", "+", Color.FromRgb(140, 110, 60), "GA_ 레이어 5개·라벨 문자 스타일·특성 틀 생성")));
            tab.Panels.Add(Panel("표시·기록",
                Big("표시 켜기/끄기", "GARCTOGGLE", "◐", Color.FromRgb(200, 160, 50), "도면 위 위반 표시·시선·라벨"),
                Big("라벨 확대", "GARCLARGE", "▣", Color.FromRgb(150, 90, 200), "도면 위 라벨 글자·선 두께·표식 크기만 2.5배로 (프로젝터·화면 공유용, 판정·도면은 그대로)"),
                Big("리포트", "GARCREPORT", "R", Color.FromRgb(200, 90, 80), "근거·측면도·이력을 HTML로 저장하고 엽니다."),
                Big("로고 패널", "GARCPANEL", "◎", Color.FromRgb(124, 106, 230), "떠 있는 로고의 검토 패널 열기/닫기 (Ctrl+.)")));
            tab.Panels.Add(Panel("버전",
                Big("버전 기록", "GARCVERSION", "✎", Color.FromRgb(70, 130, 180), "지금 상태를 버전으로 기록(작성자·사유·관련 규정). 저장할 때도 자동으로 묻습니다."),
                Big("버전 비교", "GARCCOMPARE", "⇄", Color.FromRgb(46, 150, 90), "두 버전 비교: 나란히·겹쳐·차이만, 변경 목록, 생산팀 전달(PDF/PNG)")));
        }

        private static RibbonPanel Panel(string title, params RibbonButton[] buttons)
        {
            var src = new RibbonPanelSource { Title = title };
            foreach (var b in buttons) src.Items.Add(b);
            return new RibbonPanel { Source = src };
        }

        private static RibbonButton Big(string text, string command, string glyph, Color color, string tip) => new()
        {
            Text = text,
            ShowText = true,
            ShowImage = true,
            Size = RibbonItemSize.Large,
            Orientation = System.Windows.Controls.Orientation.Vertical,
            LargeImage = Icon(glyph, color, 32),
            Image = Icon(glyph, color, 16),
            ToolTip = tip,
            CommandParameter = "_" + command + " ",
            CommandHandler = new Handler(),
        };

        private static BitmapSource Icon(string glyph, Color color, int size)
        {
            var dv = new DrawingVisual();
            using (var dc = dv.RenderOpen())
            {
                dc.DrawRoundedRectangle(new SolidColorBrush(color), null, new Rect(1, 1, size - 2, size - 2), size / 6.0, size / 6.0);
                var ft = new FormattedText(glyph, CultureInfo.CurrentUICulture, FlowDirection.LeftToRight,
                    new Typeface("Segoe UI Symbol"), size * 0.55, Brushes.White, 1.0);
                dc.DrawText(ft, new Point((size - ft.Width) / 2, (size - ft.Height) / 2));
            }
            var bmp = new RenderTargetBitmap(size, size, 96, 96, PixelFormats.Pbgra32);
            bmp.Render(dv);
            bmp.Freeze();
            return bmp;
        }

        private sealed class Handler : ICommand
        {
            public event EventHandler? CanExecuteChanged { add { } remove { } }
            public bool CanExecute(object? parameter) => true;
            public void Execute(object? parameter)
            {
                if (parameter is RibbonButton b && b.CommandParameter is string cmd)
                    AcApp.DocumentManager.MdiActiveDocument?.SendStringToExecute(cmd, true, false, true);
            }
        }
    }
}
