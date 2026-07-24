export interface DistrictPopulation {
  district_name?: string;
  resident_population: number;
  worker_population: number;
  timestamp: string;
}

export interface DistrictFloating {
  floating_population: number;
  timestamp: string;
}

export interface DistrictSales {
  industry_code: string;
  industry_name: string;
  sales_amount: number;
  timestamp: string;
}

export interface DistrictStoreCount {
  industry_code: string;
  industry_name?: string;
  store_count: number;
  timestamp: string;
}

export interface DistrictGrowthHistory {
  sales_amount: number;
  floating_population: number;
  store_count: number;
  timestamp: string;
}

export interface AreaSalePriceProxy {
  sale_price_proxy_manwon_per_m2?: number | null;
  period: string;
  source_id?: string | null;
  provider?: string | null;
  grain?: string | null;
  direct_score_allowed: boolean;
  proxy_score_allowed: boolean;
  provenance_note?: string | null;
}

export interface AreaRoneCostReference {
  period: string;
  selection_group?: string | null;
  metric_code: "rent" | "vacancy";
  metric_name?: string | null;
  metric_value?: number | null;
  unit?: string | null;
  property_type?: string | null;
  source_region_name?: string | null;
  mapping_scope?: string | null;
  mapping_method?: string | null;
  mapping_confidence?: string | null;
  source_id?: string | null;
  provider?: string | null;
  direct_value_allowed: boolean;
  proxy_score_allowed: boolean;
  engine_promotion_ready: boolean;
  forbidden_claim_ko?: string | null;
  provenance_note?: string | null;
}

export interface AreaContextScoreMetadata {
  score_type: "demand_accessibility_context";
  score_label: string;
  official_rank_eligible: false;
}

export interface IndustryAxisMetric {
  /** Internal only. User-facing components must render display_grade instead. */
  internal_value?: number | null;
  display_grade?: string | null;
}

export interface IndustryAnalysis {
  industry_code: string;
  industry_name: string;
  reference_quarter: string;
  availability: "available" | "partial" | "unavailable";
  display_grade?: string | null;
  score_applicable: boolean;
  score_version?: string | null;
  score_reason: string;
  current_sales_amount?: number | null;
  current_store_count?: number | null;
  history: Array<{
    quarter: string;
    sales_amount?: number | null;
    store_count?: number | null;
  }>;
  axes: {
    sales: IndustryAxisMetric;
    competition: IndustryAxisMetric;
    demand: IndustryAxisMetric;
    accessibility: IndustryAxisMetric;
  };
  missing_data: string[];
}

export interface AreaData extends AreaContextScoreMetadata {
  area_code: string;
  area_name: string;
  score?: number;
  grade?: string | null;
  display_grade?: string | null;
  latitude?: number;
  longitude?: number;
  district_populations?: DistrictPopulation[];
  district_floatings?: DistrictFloating[];
  district_sales?: DistrictSales[];
  district_store_counts?: DistrictStoreCount[];
  district_growth_histories?: DistrictGrowthHistory[];
  sale_price_proxies?: AreaSalePriceProxy[];
  rone_cost_references?: AreaRoneCostReference[];
  industry_analysis?: IndustryAnalysis | null;
}

export interface DashboardData extends AreaContextScoreMetadata {
  area_code: string;
  area_name: string;
  total_stores: number;
  floating_population: number;
  total_sales: number;
  sale_price_proxy_manwon_per_m2?: number | null;
  rent_reference_thousand_won_per_m2?: number | null;
  vacancy_reference_pct?: number | null;
  score?: number | null;
  grade?: string | null;
  display_grade?: string | null;
}

export interface RankingData extends AreaContextScoreMetadata {
  area_code: string;
  area_name: string;
  score?: number;
  grade?: string | null;
  display_grade?: string | null;
}

export type CommentStatus = "visible" | "hidden" | "deleted";

export interface AreaComment {
  id: number;
  area_code: string;
  industry_code?: string | null;
  parent_id?: number | null;
  body: string;
  status: CommentStatus;
  author?: { id: number; nickname: string } | null;
  created_at: string;
  updated_at: string;
  replies: AreaComment[];
}

export interface CommentPageResponse {
  items: AreaComment[];
  page: number;
  page_size: number;
  total: number;
}
