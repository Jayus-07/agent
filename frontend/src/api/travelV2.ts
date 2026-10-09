import { request } from '@/api/client'

/** 旅游 V2 独立数据契约；与旧 Itinerary / PlanState 不互转。 */

export type PlacePreferenceV2 = "must_visit" | "interested" | "excluded";
export type PlaceVerificationV2 = "verified" | "estimated" | "unknown";
export type TripPaceV2 = "relaxed" | "balanced" | "intense";
export type TripHealthStatusV2 = "ok" | "needs_attention" | "blocked";
export type TripLegModeV2 = "walk" | "drive" | "transit" | "taxi";
export type TripLegReliabilityV2 = "verified" | "estimated" | "unavailable";

export interface TripBriefV2 {
  origin: string;
  destination: string;
  timezone: string;
  start_date: string | null;
  day_count: number;
  travelers: { adults: number; children: number };
  budget: { amount: number | null; currency: "CNY" };
  pace: TripPaceV2;
  interests: string[];
  requirements: string[];
}

export interface PlaceSelectionV2 {
  place_id: string;
  name: string;
  preference: PlacePreferenceV2;
}

export interface PlaceFactsV2 {
  opening_hours?: unknown;
  ticket_price_cny?: number | null;
  rating?: number | null;
  open_status?: string | null;
  open_time_today?: string | null;
  avg_cost_cny?: number | null;
  verification: PlaceVerificationV2;
  source: string;
  observed_at: string | null;
}

export interface PlaceSnapshotV2 {
  place_id: string;
  name: string;
  lat: number | null;
  lng: number | null;
  address: string | null;
  facts: PlaceFactsV2;
}

export interface TripItemV2 {
  item_id: string;
  kind: "place" | "activity";
  activity_type?: "meal" | "rest" | "shopping" | "free_time" | "custom" | null;
  title: string;
  start_time: string | null;
  duration_min: number;
  fixed_start: boolean;
  place: PlaceSnapshotV2 | null;
  must_visit: boolean;
  locked: boolean;
  note: string;
}

export interface TripLegV2 {
  leg_id: string;
  from_item_id: string;
  to_item_id: string;
  selected_mode: TripLegModeV2;
  duration_min: number | null;
  distance_m: number | null;
  cost_cny: number | null;
  reliability: TripLegReliabilityV2;
  source: string | null;
  observed_at: string | null;
}

export interface TripDayV2 {
  day_id: string;
  date: string | null;
  title: string;
  items: TripItemV2[];
  legs: TripLegV2[];
}

export interface MerchantSnapshotV2 {
  merchant_id: string;
  name: string;
  address: string | null;
  lat: number | null;
  lng: number | null;
  rating: number | null;
  open_status: string | null;
  open_time_today: string | null;
  avg_cost_cny: number | null;
  source: string;
  observed_at: string;
}

export interface LodgingArrangementV2 {
  selection_id: string;
  merchant: MerchantSnapshotV2;
  check_in: string;
  check_out: string;
  source: string;
  queried_at: string;
  verification: PlaceVerificationV2;
  booking_status: "not_booked";
  nightly_price_cny: number | null;
}

export interface IntercityTrainArrangementV2 {
  selection_id: string;
  train_code: string;
  travel_date: string;
  departure_station: string;
  arrival_station: string;
  departure_time: string;
  arrival_time: string;
  duration_min: number;
  tickets: Record<string, string | number>;
  fares: Record<string, number | string>;
  ticket_source: string;
  fare_source: string | null;
  queried_at: string;
  verification: "verified" | "delayed" | "unknown";
  booking_status: "not_booked";
}

export type TravelSearchStatusV2 =
  | "success" | "no_results" | "business_failure" | "network_timeout"
  | "provider_unavailable" | "disabled";

export type TripEditOperationV2 =
  | { op: "add_activity"; day_id: string; activity_type: NonNullable<TripItemV2["activity_type"]>; title: string; start_time?: string | null; duration_min: number; note?: string; position?: number }
  | { op: "add_place"; day_id: string; selection_id: string; place: PlaceSnapshotV2; start_time?: string | null; duration_min: number; position?: number }
  | { op: "replace_place"; item_id: string; selection_id: string; place: PlaceSnapshotV2 }
  | { op: "update_item"; item_id: string; start_time?: string | null; duration_min?: number; note?: string }
  | { op: "remove_item"; item_id: string }
  | { op: "move_item"; item_id: string; target_day_id: string; position?: number }
  | { op: "reorder_day"; day_id: string; item_ids: string[] }
  | { op: "add_day"; title?: string }
  | { op: "remove_day"; day_id: string }
  | { op: "set_leg_mode"; from_item_id: string; to_item_id: string; selected_mode: TripLegModeV2 }

export interface TravelSearchResponseV2<T extends Record<string, unknown> = Record<string, unknown>> {
  kind: "food" | "hotel" | "train" | "places";
  status: TravelSearchStatusV2;
  source: string;
  queried_at: string;
  message: string;
  results: T[];
  disclosure: string;
}

export interface PlaceSearchResultV2 extends Record<string, unknown> {
  selection_id: string;
  place: PlaceSnapshotV2;
  category: string | null;
  source: "tencent:lbs";
  queried_at: string;
}

export interface MerchantSearchResultV2 extends Record<string, unknown> {
  selection_id: string;
  merchant_id: string;
  name: string;
  address: string | null;
  rating: number | null;
  open_status: string | null;
  open_time_today: string | null;
  lat: number | null;
  lng: number | null;
  avg_cost_cny: number | null;
  source: string;
  queried_at: string;
  nightly_price_cny?: null;
  room_availability?: "unknown";
}

export interface TrainSearchResultV2 extends Record<string, unknown> {
  selection_id: string;
  train_code: string;
  travel_date: string;
  departure_station: string;
  arrival_station: string;
  departure_time: string;
  arrival_time: string;
  duration_min: number | null;
  tickets: Record<string, string | number>;
  fares: Record<string, number | string>;
  ticket_source: string;
  fare_source: string | null;
  queried_at: string;
  verification: "verified" | "delayed" | "unknown";
  duration: string;
}

export interface TripArrangementsV2 {
  lodgings: LodgingArrangementV2[];
  intercity_trains: IntercityTrainArrangementV2[];
}

export interface TripTotalsV2 {
  currency: "CNY";
  estimated_cost: number | null;
  transit_min: number;
  distance_m: number | null;
}

export interface TripHealthIssueV2 {
  code: string;
  severity: "info" | "warning" | "error";
  day_id?: string;
  item_id?: string;
  message: string;
}

export interface TripHealthV2 {
  status: TripHealthStatusV2;
  issues: TripHealthIssueV2[];
}

export interface TripDocumentV2 {
  schema_version: 2;
  brief: TripBriefV2;
  selections: PlaceSelectionV2[];
  days: TripDayV2[];
  arrangements: TripArrangementsV2;
  totals: TripTotalsV2;
  health: TripHealthV2;
}

export interface TravelTripV2 {
  trip_id: string;
  title: string;
  status: "active" | "archived";
  revision: number;
  document: TripDocumentV2;
  source_template_id: string | null;
  source_template_version: number | null;
  created_at: string;
  updated_at: string;
}

function idempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `travel-v2-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function post<T>(path: string, body: object, key?: string): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body,
    ...(key ? { headers: { "Idempotency-Key": key } } : {}),
  });
}

export function fetchTravelTripsV2(): Promise<{ trips: TravelTripV2[] }> {
  return request("/api/travel/v2/trips?limit=50");
}

export function fetchTravelTripV2(tripId: string): Promise<TravelTripV2> {
  return request(`/api/travel/v2/trips/${encodeURIComponent(tripId)}`);
}

export function fetchTravelTripRevisionsV2(tripId: string): Promise<{
  trip_id: string;
  revisions: TravelTripRevisionV2[];
}> {
  return request(`/api/travel/v2/trips/${encodeURIComponent(tripId)}/revisions`);
}

export function archiveTravelTripV2(tripId: string): Promise<{ trip_id: string; status: 'archived'; saved: true }> {
  return request(`/api/travel/v2/trips/${encodeURIComponent(tripId)}`, { method: 'DELETE' });
}

export function createTravelTripV2(input: CreateTravelTripV2, key = idempotencyKey()): Promise<TravelTripV2> {
  return post('/api/travel/v2/trips', input, key);
}

export function saveTravelTripDocumentV2(tripId: string, input: EditTravelTripV2, key = idempotencyKey()): Promise<{
  saved: true; revision: number; document: TripDocumentV2;
}> {
  return request(`/api/travel/v2/trips/${encodeURIComponent(tripId)}/document`, {
    method: 'PUT',
    body: input,
    headers: { 'Idempotency-Key': key },
  });
}

export function applyTravelTripEditV2(tripId: string, input: {
  expected_revision: number;
  change_summary: string;
  operation: TripEditOperationV2;
}, key = idempotencyKey()): Promise<{ saved: true; trip_id: string; revision: number; document: TripDocumentV2; status: "active" }> {
  return post(`/api/travel/v2/trips/${encodeURIComponent(tripId)}/edits`, input, key);
}

export function fetchTravelTemplatesV2(): Promise<{ templates: TravelTemplateV2[] }> {
  return request('/api/travel/v2/templates?limit=50');
}

export function fetchTravelTemplateV2(key: string): Promise<TravelTemplateV2> {
  return request(`/api/travel/v2/templates/${encodeURIComponent(key)}`);
}

export function copyTravelTemplateV2(key: string): Promise<TravelTripV2> {
  return post(`/api/travel/v2/templates/${encodeURIComponent(key)}/copy`, {}, idempotencyKey());
}

export function searchTravelFoodV2(city: string): Promise<TravelSearchResponseV2<MerchantSearchResultV2>> {
  return post("/api/travel/v2/search/food", { city });
}

export function searchTravelPlacesV2(city: string, keyword: string): Promise<TravelSearchResponseV2<PlaceSearchResultV2>> {
  return post("/api/travel/v2/search/places", { city, keyword });
}

export function searchTravelHotelsV2(city: string): Promise<TravelSearchResponseV2<MerchantSearchResultV2>> {
  return post("/api/travel/v2/search/hotels", { city });
}

export function searchTravelTrainsV2(input: {
  from_station: string; to_station: string; travel_date: string;
}): Promise<TravelSearchResponseV2<TrainSearchResultV2>> {
  return post("/api/travel/v2/search/trains", input);
}

export function addTravelMealV2(tripId: string, input: {
  expected_revision: number; day_id: string; meal_period: "lunch" | "dinner";
  selection_id: string; merchant: MerchantSnapshotV2;
}): Promise<{ saved: true; revision: number; document: TripDocumentV2 }> {
  return post(`/api/travel/v2/trips/${encodeURIComponent(tripId)}/selections/meals`, input, idempotencyKey());
}

export function selectTravelLodgingV2(tripId: string, input: {
  expected_revision: number; lodging: LodgingArrangementV2;
}): Promise<{ saved: true; revision: number; document: TripDocumentV2 }> {
  return post(`/api/travel/v2/trips/${encodeURIComponent(tripId)}/selections/lodgings`, input, idempotencyKey());
}

export function selectTravelTrainV2(tripId: string, input: {
  expected_revision: number; train: IntercityTrainArrangementV2;
}): Promise<{ saved: true; revision: number; document: TripDocumentV2 }> {
  return post(`/api/travel/v2/trips/${encodeURIComponent(tripId)}/selections/intercity-trains`, input, idempotencyKey());
}

export function restoreTravelTripV2(tripId: string, input: {
  expected_revision: number; target_revision: number;
}): Promise<{ saved: true; revision: number; document: TripDocumentV2 }> {
  return post(`/api/travel/v2/trips/${encodeURIComponent(tripId)}/restore`, input, idempotencyKey());
}

export interface TravelTripRevisionV2 {
  trip_id: string;
  revision: number;
  parent_revision: number | null;
  snapshot: TripDocumentV2;
  change_type: string;
  change_summary: string;
  actor_id: string;
  created_at: string;
}

export interface TravelTemplateV2 {
  template_id: string;
  slug: string;
  title: string;
  destination: string;
  summary: string;
  cover_image_url: string | null;
  tags: string[];
  version: number;
  status: "draft" | "published" | "archived";
  sort_weight: number;
  content: TripDocumentV2;
  created_by: string;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateTravelTripV2 {
  title: string;
  document: TripDocumentV2;
}

export interface EditTravelTripV2 {
  expected_revision: number;
  command_type: "replace_document" | "structured_edit" | "ai_edit" | "restore_revision";
  change_summary: string;
  document: TripDocumentV2;
}
