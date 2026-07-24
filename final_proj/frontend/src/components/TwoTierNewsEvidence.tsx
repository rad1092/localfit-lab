import { ExternalLink, Eye, Newspaper, ShieldCheck } from "lucide-react";

export type NewsEvidenceItem = {
  evidence_id?: string;
  source_group?: string;
  provider?: string;
  title?: string;
  summary?: string;
  original_url?: string;
  published_date?: string;
  location_scope_label?: string;
  condition_fit?: string;
  selection_reason?: string;
  decision_use?: string;
  reference_use?: string;
  applicability_limit?: string;
  evidence_tier?: "decision_support" | "reference_monitoring";
  evidence_tier_label?: string;
  structured_score_impact?: string;
  eligible_for_decision?: boolean;
  citation_index?: number;
};

type Props = {
  items: NewsEvidenceItem[];
  className?: string;
};

function SourceTypeLabel({ item }: { item: NewsEvidenceItem }) {
  return (
    <p className="inline-flex rounded bg-muted px-1.5 py-0.5 font-bold text-foreground">
      {item.source_group === "news_search"
        ? "기사"
        : item.source_group
          ? "보도자료"
          : "기사·보도자료"}
    </p>
  );
}

function EvidenceRows({
  items,
  monitoring,
}: {
  items: NewsEvidenceItem[];
  monitoring: boolean;
}) {
  return (
    <div className="mt-3 divide-y border-y">
      {items.map((item, index) => (
        <article
          key={item.evidence_id || `${item.original_url}-${index}`}
          className="grid gap-2 py-4 md:grid-cols-[150px_minmax(0,1fr)] md:gap-6"
        >
          <div className="text-xs leading-5 text-muted-foreground">
            <SourceTypeLabel item={item} />
            <p className="mt-1 font-semibold text-foreground">
              {item.provider || "공식 자료"}
            </p>
            <p>{item.published_date || "발행일 미표기"}</p>
            {item.location_scope_label && <p>{item.location_scope_label}</p>}
          </div>
          <div className="min-w-0">
            {item.original_url ? (
              <a
                href={item.original_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex max-w-full items-start gap-1.5 font-semibold leading-6 text-primary hover:underline"
              >
                <span className="break-words">{item.title || "원문 보기"}</span>
                <ExternalLink className="mt-1 h-3.5 w-3.5 shrink-0" />
              </a>
            ) : (
              <p className="font-semibold leading-6">{item.title}</p>
            )}

            {monitoring ? (
              <>
                {item.selection_reason && (
                  <p className="mt-2 text-sm leading-6">
                    선정 이유: {item.selection_reason}
                  </p>
                )}
                {item.reference_use && (
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    참고할 내용: {item.reference_use}
                  </p>
                )}
                <p className="mt-2 rounded-lg border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-950 dark:bg-amber-950/20 dark:text-amber-100">
                  판단 제외 사유:{" "}
                  {item.applicability_limit ||
                    "직접 근거가 부족하여 점수·등급·추천 판단에는 사용하지 않음"}
                </p>
              </>
            ) : (
              <>
                {(item.condition_fit || item.selection_reason) && (
                  <p className="mt-2 text-sm leading-6">
                    조건 적합성: {item.condition_fit || item.selection_reason}
                  </p>
                )}
                {item.decision_use && (
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    판단에 사용: {item.decision_use}
                  </p>
                )}
              </>
            )}
            {item.summary && (
              <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">
                {item.summary}
              </p>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

export function TwoTierNewsEvidence({ items, className = "" }: Props) {
  const decisionItems = items.filter(
    (item) => item.evidence_tier !== "reference_monitoring",
  );
  const monitoringItems = items.filter(
    (item) => item.evidence_tier === "reference_monitoring",
  );

  if (!items.length) return null;

  return (
    <section className={`rounded-2xl border bg-card p-4 shadow-sm sm:p-6 ${className}`}>
      <div className="flex items-center gap-2">
        <Newspaper className="h-5 w-5 text-primary" />
        <h2 className="text-xl font-bold">두 단계 기사·보도자료</h2>
      </div>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        모든 자료는 정형 점수·등급과 분리합니다. 판단 근거는 원문이 직접
        뒷받침하는 범위에서만 사용하고, 참고·모니터링 자료는 점수·등급·추천
        판단에 사용하지 않습니다.
      </p>

      {decisionItems.length > 0 && (
        <div className="mt-5">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-emerald-600" />
            <h3 className="font-bold">1단계 · 판단 근거</h3>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            위치·업종 또는 지속적인 입지 변화와 원문 내용이 직접 연결된 자료입니다.
          </p>
          <EvidenceRows items={decisionItems} monitoring={false} />
        </div>
      )}

      {monitoringItems.length > 0 && (
        <div className="mt-6">
          <div className="flex flex-wrap items-center gap-2">
            <Eye className="h-4 w-4 text-amber-600" />
            <h3 className="font-bold">2단계 · 참고·모니터링</h3>
            <span className="rounded-full border border-amber-300/60 bg-amber-50 px-2 py-0.5 text-[11px] font-bold text-amber-900 dark:bg-amber-950/20 dark:text-amber-100">
              점수·등급·추천 판단 미반영
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            지역 관련성은 있으나 업종·예산·지속성 또는 직접 공간 연결이 부족한
            자료입니다.
          </p>
          <EvidenceRows items={monitoringItems} monitoring />
        </div>
      )}
    </section>
  );
}
