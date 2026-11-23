/**
 * travelServices.ts — 商旅与文旅扩展服务 API
 *
 * 提供统一的文旅服务配置读取、热加载保存与连通性测试。
 */
import { request, mutationRequest } from "./client";

export interface TravelServiceItem {
  name: string;
  configured: boolean;
  enabled?: boolean;
  key_preview?: string;
  description: string;
  docs_url?: string;
}

export interface TravelServicesConfigResponse {
  status: string;
  services: {
    amap: TravelServiceItem;
    xiaohongshu: TravelServiceItem;
    train_12306: TravelServiceItem;
    weather: TravelServiceItem;
    zhihu: TravelServiceItem;
  };
  guide_source_priority: "xiaohongshu" | "zhihu" | "local";
}

export interface TravelServicesConfigUpdatePayload {
  amap_key?: string;
  xiaohongshu_api_key?: string;
  xiaohongshu_enabled?: boolean;
  weather_api_key?: string;
  train_12306_enabled?: boolean;
  zhihu_enabled?: boolean;
  guide_source_priority?: "xiaohongshu" | "zhihu" | "local";
}

export interface TravelServiceTestResult {
  service: string;
  ok: boolean;
  message: string;
}

export async function getTravelServicesConfig(): Promise<TravelServicesConfigResponse> {
  return request<TravelServicesConfigResponse>("/admin/travel-services/config");
}

export async function updateTravelServicesConfig(
  payload: TravelServicesConfigUpdatePayload,
): Promise<{ status: string; message: string; actor?: string }> {
  return mutationRequest("/admin/travel-services/config", {
    operation: "update_travel_services_config",
    method: "PUT",
    body: payload,
  });
}

export async function testTravelService(serviceId: string): Promise<TravelServiceTestResult> {
  return mutationRequest<TravelServiceTestResult>(
    `/admin/travel-services/test?service_id=${encodeURIComponent(serviceId)}`,
    {
      operation: `test_travel_service_${serviceId}`,
      method: "POST",
    },
  );
}
