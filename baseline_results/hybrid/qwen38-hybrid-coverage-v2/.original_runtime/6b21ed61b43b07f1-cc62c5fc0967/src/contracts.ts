export interface SourceInput {
  url: string;
  name?: string;
  itemSelector?: string | null;
  fieldSelectors?: Record<string, string>;
  searchUrlTemplate?: string | null;
  searchEntryUrlTemplate?: string | null;
  searchInputSelector?: string | null;
  searchSubmitSelector?: string | null;
  searchSubmitAction?: string | null;
}

export interface ExtractProductsRequest {
  html: string;
  pageUrl: string;
  source?: SourceInput;
  query?: string | null;
  itemSelector?: string | null;
  fieldSelectors?: Record<string, string>;
  limit?: number;

  /**
   * Для обычного collection можно оставить false.
   * Для onboarding true: вернуть topCandidates и частичные поля.
   */
  includeCandidates?: boolean;
}

export interface ProductRow {
  name: string;
  price: number;
  product_url: string;
  delivery_time?: string;
  rating?: number;
  in_stock?: boolean;
  image_url?: string;
  availability?: string;
}

export interface SearchProduct {
  name: string;
  price: number;
  url: string;
  delivery_time?: string;
  rating?: number;
  in_stock?: boolean;
}

export interface ExtractionDiagnostics {
  blockedMarkers: string[];
  emptyReason?: string;
  candidateCount: number;
  rowCount: number;
  rejectedRows: Record<string, number>;
  debug: string[];
  candidates?: CandidateDiagnostics;
}

export interface QueryTag {
  value: string;
  normalized: string;
  kind: "word" | "measure" | "number";
  required: boolean;
}

export interface QueryRelevanceDiagnostics {
  enabled: boolean;
  queryTags: QueryTag[];
  beforeCount: number;
  afterCount: number;
  rejectedCount: number;
  threshold: number;
  rejected: Array<{
    name: string;
    score: number;
    reason: string;
  }>;
}

export interface ExtractProductsResult {
  status: "ok" | "empty" | "blocked";
  route: string;
  pageUrl: string;
  queryTags?: QueryTag[];
  rows: ProductRow[];
  products: SearchProduct[];
  diagnostics: ExtractionDiagnostics & {
    queryRelevance?: QueryRelevanceDiagnostics;
  };
}

export interface SupplierProfile {
  domain: string;
  displayName: string;
  itemSelector?: string;
  fieldSelectors?: Record<string, string>;
  productUrlHints?: string[];
}

export interface SourceDraft {
  url: string;
  name: string;
  is_available: boolean;
  item_selector?: string | null;
  field_selectors?: Record<string, string>;
  search_url_template?: string | null;
  search_entry_url_template?: string | null;
  search_input_selector?: string | null;
  search_submit_selector?: string | null;
  search_submit_action?: string | null;
}

export interface OnboardingRequest extends ExtractProductsRequest {
  catalogUrl?: string | null;
  sampleQuery?: string | null;
  searchUrlTemplate?: string | null;
}

export interface OnboardingResult {
  status: "ready_for_save" | "manual_required" | "blocked" | "empty";
  diagnosticStatus: number;
  message: string;
  route: string;
  pageUrl: string;
  sourceDraft: SourceDraft | null;
  fields: Record<string, unknown>;
  rows: ProductRow[];
  diagnostics: ExtractionDiagnostics & {
    selectedRowIndex?: number;
  };
}

export interface ProductCandidateFieldDiagnostics {
  value?: unknown;
  confidence: number;
  reason: string;
  raw: string[];
}

export interface ProductCandidate {
  index: number;
  selector: string;
  htmlPreview: string;
  textPreview: string;
  score: number;
  reasons: string[];
  fields: Partial<ProductRow>;
  fieldDiagnostics: Record<string, ProductCandidateFieldDiagnostics>;
  missingFields: string[];
  rejectReason?: string;
}

export interface CandidateDiagnostics {
  candidateCount: number;
  validRowCount: number;
  rejectedCandidateCount: number;
  rejectReasons: Record<string, number>;
  topCandidates: ProductCandidate[];
}
