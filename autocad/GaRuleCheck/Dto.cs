// 엔진(Python)과 주고받는 JSON. 필드 이름은 snake_case로 자동 변환된다(JsonNamingPolicy.SnakeCaseLower).
// 명세: docs/input-contract.md, engine/garc/contract.py
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GaRuleCheck
{
    public static class Contract
    {
        // 레이어 → 역할 (engine/garc/contract.py LAYER_ROLES와 같아야 한다)
        public static readonly Dictionary<string, string> LayerRoles = new()
        {
            ["GA_HULL"] = "hull",
            ["GA_EYE"] = "eye",
            ["GA_OBST"] = "obstruction",
            ["GA_BHD_COLL"] = "collision_bhd",
            ["GA_BULB"] = "bulb",
        };

        public static bool IsWatchedLayer(string layer) => LayerRoles.ContainsKey(layer.Trim().ToUpperInvariant());

        /// 실도면 역할 이름(엔진 contract.py ROLE_ALIASES와 같은 뜻). 마법사·명령에서 쓰는 한글 이름 → 엔진 이름.
        public static readonly (string Role, string Ko)[] Roles =
        {
            ("hull", "선체"), ("obstruction", "장애물"), ("collision_bhd", "충돌격벽"), ("bulb", "구상선수"), ("eye", "조종위치"),
        };

        public static string RoleKo(string role) => Roles.FirstOrDefault(r => r.Role == role).Ko ?? role;

        /// 도면 특성 GARC_LAYER_ROLES('컨테이너=장애물; …')의 레이어 이름만 — 어떤 객체를 보낼지 고르는 용도.
        /// 역할 해석·형식 검사는 엔진이 한다.
        public static HashSet<string> LayerRoleNames(IReadOnlyDictionary<string, string> props)
        {
            var set = new HashSet<string>(StringComparer.Ordinal);
            var text = props.FirstOrDefault(kv => kv.Key.Trim().Equals("GARC_LAYER_ROLES", StringComparison.OrdinalIgnoreCase)).Value ?? "";
            foreach (var chunk in text.Replace("\n", ";").Split(';'))
            {
                int eq = chunk.LastIndexOf('=');
                if (eq > 0) set.Add(chunk[..eq].Trim().ToUpperInvariant());
            }
            return set;
        }

        public static readonly JsonSerializerOptions Json = new()
        {
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            PropertyNameCaseInsensitive = true,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };
    }

    public sealed class RawEntity
    {
        public string Handle { get; set; } = "";
        public string Layer { get; set; } = "";
        public string Type { get; set; } = "";
        public List<double[]> Points { get; set; } = new();
        /// 꼭짓점 i→i+1 구간의 bulge (호가 하나도 없으면 null → JSON에서 생략)
        public List<double>? Bulges { get; set; }
        /// 닫힌 폴리선 여부 (폴리선이 아니면 null)
        public bool? Closed { get; set; }
        /// 실도면 역할 지정(레이어 대신 객체에 붙인 역할, 없으면 null → 생략)
        public string? Role { get; set; }
        /// 블록을 펼친 하위 객체면 원래 블록(INSERT)의 handle. 이때 Handle은 "<블록>.<번호>"
        public string? Block { get; set; }
    }

    public sealed class RawDrawing
    {
        public string Schema { get; set; } = "garc.raw/1";
        public Dictionary<string, string> Source { get; set; } = new();
        public int Insunits { get; set; }
        public Dictionary<string, string> Props { get; set; } = new();
        public List<RawEntity> Entities { get; set; } = new();
        /// 모델 공간의 레이어별 객체 수 (GA_ 이름과 비슷한 레이어를 엔진이 감지하도록, 판단은 엔진)
        public Dictionary<string, int> LayerCounts { get; set; } = new();
    }

    public sealed class CheckRequest
    {
        public string DocId { get; set; } = "";
        public RawDrawing Raw { get; set; } = new();
    }

    public sealed class OverlayDto
    {
        public string Kind { get; set; } = "";
        public string? Status { get; set; }
        public List<double[]> Points { get; set; } = new();
        public string? Text { get; set; }
        public double? Height { get; set; }   // 라벨 글자 높이 [m]
    }

    public sealed class ReportResponse
    {
        public string Html { get; set; } = "";
    }

    public sealed class RuleResultDto
    {
        public string RuleId { get; set; } = "";
        public string TitleKo { get; set; } = "";
        public string Ref { get; set; } = "";
        public string Status { get; set; } = "";
        public string StatusKo { get; set; } = "";
        public string MessageKo { get; set; } = "";
        public JsonElement? Actual { get; set; }
        public JsonElement? Limit { get; set; }
        public string Unit { get; set; } = "m";
        public string? Condition { get; set; }
        public List<string> Targets { get; set; } = new();
        public string? SuggestionKo { get; set; }
        public List<string> Limitations { get; set; } = new();
        public List<OverlayDto> Overlays { get; set; } = new();
        /// 객체별 판정(마우스 오버 툴팁·체크리스트). 판정은 엔진이 한다.
        public List<ElementCheckDto> Elements { get; set; } = new();
        public bool Cached { get; set; }
    }

    /// 요소(도면 객체) 하나의 판정 — 상태는 pass / fail / unknown(확인 필요) 셋뿐.
    public sealed class ElementCheckDto
    {
        public string ElementId { get; set; } = "";
        public string RuleId { get; set; } = "";
        public string RuleName { get; set; } = "";
        public string Role { get; set; } = "";
        public string RoleKo { get; set; } = "";
        public string Status { get; set; } = "";
        public string Required { get; set; } = "";
        public string Actual { get; set; } = "";
        public string? Diff { get; set; }
        public string? Guide { get; set; }
        public double? Margin { get; set; }
    }

    public sealed class DiffDto
    {
        public bool Initial { get; set; }
        public List<string> Added { get; set; } = new();
        public List<string> Removed { get; set; } = new();
        public List<string> Modified { get; set; } = new();
        public List<string> ShipChanged { get; set; } = new();
        public bool ConditionsChanged { get; set; }
    }

    public sealed class TransitionDto
    {
        public string RuleId { get; set; } = "";
        public string? From { get; set; }
        public string To { get; set; } = "";
        public string Kind { get; set; } = "";
    }

    public sealed class CheckResponse
    {
        public string Overall { get; set; } = "";
        public string OverallKo { get; set; } = "";
        public string DisclaimerKo { get; set; } = "";
        public Dictionary<string, int> Summary { get; set; } = new();
        public List<RuleResultDto> Results { get; set; } = new();
        public int Revision { get; set; }
        public int? SameAsRevision { get; set; }
        public DiffDto Diff { get; set; } = new();
        public List<string> AffectedRules { get; set; } = new();
        public List<string> RerunRules { get; set; } = new();
        public List<string> CachedRules { get; set; } = new();
        public List<TransitionDto> Transitions { get; set; } = new();
        public double? UnitsScale { get; set; }
        /// 판정에 필요한 입력의 준비 상태(엔진 0.4+). 옛 엔진이면 null
        public ReadinessDto? Readiness { get; set; }
    }

    // ── 실도면 연결: 준비 상태·치수표 읽기 ──────────────────────────────────────

    public sealed class ReadinessDto
    {
        public int Ready { get; set; }
        public int Total { get; set; }
        public bool AllReady { get; set; }
        public List<ReadinessItemDto> Items { get; set; } = new();
        public List<string> ProblemsKo { get; set; } = new();
    }

    public sealed class ReadinessItemDto
    {
        public string Key { get; set; } = "";
        public string NameKo { get; set; } = "";
        public bool Required { get; set; }
        public List<string> Rules { get; set; } = new();
        /// ok / missing / invalid / default / optional
        public string Status { get; set; } = "";
        public string? ValueKo { get; set; }
        public string? HintKo { get; set; }
        public string? ProblemKo { get; set; }
    }

    public sealed class TextDto
    {
        public string Text { get; set; } = "";
        public double X { get; set; }
        public double Y { get; set; }
        public double H { get; set; }
    }

    public sealed class ParticularsRequest
    {
        public List<TextDto> Texts { get; set; } = new();
    }

    public sealed class ParticularItemDto
    {
        public string NameKo { get; set; } = "";
        /// found / conflict / missing
        public string Status { get; set; } = "";
        /// 숫자(치수·축척 N) 또는 문자(선종)
        public JsonElement? Value { get; set; }

        public string ValueText => Value is JsonElement v
            ? v.ValueKind == JsonValueKind.Number ? v.GetDouble().ToString("0.###", System.Globalization.CultureInfo.InvariantCulture)
            : v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : ""
            : "";
    }

    public sealed class ConditionDto
    {
        public string Name { get; set; } = "";
        public double TAft { get; set; }
        public double TFwd { get; set; }
    }

    public sealed class ParticularsResponse
    {
        public Dictionary<string, ParticularItemDto> Items { get; set; } = new();
        public Dictionary<string, string> SuggestedProps { get; set; } = new();
        public List<ConditionDto> SuggestedConditions { get; set; } = new();
        public List<string> HumanInputsKo { get; set; } = new();
    }

    // ── 버전 비교 (POST /v1/compare) ─────────────────────────────────────────

    public sealed class CompareRequest
    {
        public RawDrawing A { get; set; } = new();
        public RawDrawing B { get; set; } = new();
        public List<string> Labels { get; set; } = new();
    }

    public sealed class RuleBriefDto
    {
        public string RuleId { get; set; } = "";
        public string TitleKo { get; set; } = "";
        public string Status { get; set; } = "";
        public string StatusKo { get; set; } = "";
        public string MessageKo { get; set; } = "";
    }

    public sealed class VersionSummaryDto
    {
        public string Label { get; set; } = "";
        public string Overall { get; set; } = "";
        public string OverallKo { get; set; } = "";
        public Dictionary<string, int> Summary { get; set; } = new();
        public int FailElements { get; set; }
        public int UnknownElements { get; set; }
        public List<RuleBriefDto> Rules { get; set; } = new();
        public double? UnitsScale { get; set; }
    }

    public sealed class ChangeRefDto
    {
        public string RuleId { get; set; } = "";
        public string RuleName { get; set; } = "";
        public string Text { get; set; } = "";
    }

    /// 변경 항목 한 건. kind = added / removed / modified / ship / conditions. 좌표는 m(엔진 좌표).
    public sealed class ChangeDto
    {
        public int Number { get; set; }
        public string Kind { get; set; } = "";
        public string? ElementId { get; set; }
        /// 펼친 블록을 한 항목으로 묶은 변경이면 하위 객체 handle 목록(엔진 0.4+). 아니면 null
        public List<string>? Members { get; set; }
        /// 그림에서 이 변경에 해당하는 형상 handle들(묶인 블록이면 하위 객체 전부)
        public IEnumerable<string> Handles => Members is { Count: > 0 } m ? m : ElementId != null ? new[] { ElementId } : Array.Empty<string>();
        public string? Role { get; set; }
        public string RoleKo { get; set; } = "";
        public string DescKo { get; set; } = "";
        public string BeforeKo { get; set; } = "";
        public string AfterKo { get; set; } = "";
        public List<ChangeRefDto> Resolved { get; set; } = new();
        public List<ChangeRefDto> Introduced { get; set; } = new();
        /// 이번 변경으로 판정 못 함·적용 제외로 바뀐 것 — '해결'이 아니다(확인 필요)
        public List<ChangeRefDto> NeedsCheck { get; set; } = new();
        public List<string> RelatedRules { get; set; } = new();
        public double[]? Center { get; set; }
        public double[]? Bbox { get; set; }
    }

    public sealed class IndirectDto
    {
        public string ElementId { get; set; } = "";
        /// 판정 못 하게 된 객체는 규칙마다 한 줄로 묶여 온다(element_ids, count)
        public List<string> ElementIds { get; set; } = new();
        public int Count { get; set; } = 1;
        public string RoleKo { get; set; } = "";
        public string RuleId { get; set; } = "";
        public string RuleName { get; set; } = "";
        public string? From { get; set; }
        public string? To { get; set; }
        public string Text { get; set; } = "";
    }

    public sealed class GeomDto
    {
        public string Handle { get; set; } = "";
        public string Role { get; set; } = "";
        public List<double[]> Points { get; set; } = new();
        public bool Closed { get; set; }
        public string? Status { get; set; }
    }

    public sealed class GeometryPairDto
    {
        public List<GeomDto> A { get; set; } = new();
        public List<GeomDto> B { get; set; } = new();
    }

    public sealed class ViolationsDto
    {
        public int Before { get; set; }
        public int After { get; set; }
    }

    public sealed class CompareResponse
    {
        public string Schema { get; set; } = "";
        public VersionSummaryDto A { get; set; } = new();
        public VersionSummaryDto B { get; set; } = new();
        public List<ChangeDto> Changes { get; set; } = new();
        public List<ChangeRefDto> ResolvedRules { get; set; } = new();
        public List<ChangeRefDto> IntroducedRules { get; set; } = new();
        public List<ChangeRefDto> NeedsCheckRules { get; set; } = new();
        public List<IndirectDto> Indirect { get; set; } = new();
        public ViolationsDto Violations { get; set; } = new();
        /// 확인 필요(판정 못 함) 요소 수 — 이전/이후
        public ViolationsDto Unknown { get; set; } = new();
        public GeometryPairDto Geometry { get; set; } = new();
        public double[]? Bounds { get; set; }
        public double? UnitsScale { get; set; }
        public bool Same { get; set; }
    }

    // ── 버전 기록 (<도면이름>.garc/versions.json) ─────────────────────────────

    public sealed class VersionEntry
    {
        public int Version { get; set; }
        public string File { get; set; } = "";
        public string Created { get; set; } = "";
        public string Author { get; set; } = "";
        /// rule_violation / feedback / other
        public string Reason { get; set; } = "other";
        /// 관련 규정 조항 또는 피드백 내용
        public string Reference { get; set; } = "";
        public string Note { get; set; } = "";
        public string? Overall { get; set; }
        public int? FailElements { get; set; }
        /// 스냅샷 내용 지문 — 바뀐 게 없으면 새 버전을 만들지 않는다
        public string? Hash { get; set; }
    }

    public sealed class VersionIndex
    {
        public string Schema { get; set; } = "garc.versions/1";
        public string Drawing { get; set; } = "";
        public List<VersionEntry> Versions { get; set; } = new();
    }
}
