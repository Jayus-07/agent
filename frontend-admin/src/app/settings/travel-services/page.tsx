"use client";

import { useEffect, useState } from "react";
import {
  AlertCircle,
  Camera,
  CheckCircle2,
  ExternalLink,
  Flame,
  Globe,
  KeyRound,
  MapPin,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Sparkles,
  Train,
  Utensils,
  CloudSun,
} from "lucide-react";
import RoleGate from "@/components/auth/RoleGate";
import PageHeader from "@/components/layout/PageHeader";
import {
  type TravelServicesConfigResponse,
  type TravelServiceTestResult,
  getTravelServicesConfig,
  testTravelService,
  updateTravelServicesConfig,
} from "@/api/travelServices";

export default function TravelServicesSettingsPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 表单状态
  const [amapKey, setAmapKey] = useState("");
  const [xhsKey, setXhsKey] = useState("");
  const [xhsEnabled, setXhsEnabled] = useState(true);
  const [weatherKey, setWeatherKey] = useState("");
  const [train12306Enabled, setTrain12306Enabled] = useState(true);
  const [zhihuEnabled, setZhihuEnabled] = useState(true);
  const [priority, setPriority] = useState<"xiaohongshu" | "zhihu" | "local">("xiaohongshu");

  // 服务原始状态缓存
  const [config, setConfig] = useState<TravelServicesConfigResponse | null>(null);

  // 测试状态
  const [testingService, setTestingService] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TravelServiceTestResult | null>(null);

  async function loadConfig() {
    setLoading(true);
    setError(null);
    try {
      const res = await getTravelServicesConfig();
      setConfig(res);
      setXhsEnabled(res.services.xiaohongshu.enabled ?? true);
      setTrain12306Enabled(res.services.train_12306.enabled ?? true);
      setZhihuEnabled(res.services.zhihu.enabled ?? true);
      setPriority(res.guide_source_priority || "xiaohongshu");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取服务配置失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadConfig();
  }, []);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaveSuccess(null);
    try {
      const payload: Parameters<typeof updateTravelServicesConfig>[0] = {
        xiaohongshu_enabled: xhsEnabled,
        train_12306_enabled: train12306Enabled,
        zhihu_enabled: zhihuEnabled,
        guide_source_priority: priority,
      };
      if (amapKey.trim()) payload.amap_key = amapKey.trim();
      if (xhsKey.trim()) payload.xiaohongshu_api_key = xhsKey.trim();
      if (weatherKey.trim()) payload.weather_api_key = weatherKey.trim();

      const res = await updateTravelServicesConfig(payload);
      setSaveSuccess(res.message || "配置已热更新并即时生效！");
      setAmapKey("");
      setXhsKey("");
      setWeatherKey("");
      await loadConfig();
      setTimeout(() => setSaveSuccess(null), 4000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存配置失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest(serviceId: string, testKey?: string) {
    setTestingService(serviceId);
    setTestResult(null);
    try {
      const res = await testTravelService(serviceId, testKey);
      setTestResult(res);
    } catch (err) {
      setTestResult({
        service: serviceId,
        ok: false,
        message: err instanceof Error ? err.message : "连通性测试异常",
      });
    } finally {
      setTestingService(null);
    }
  }

  return (
    <RoleGate minRole="admin">
      <div className="space-y-6 p-6">
        <PageHeader
          title="商旅与文旅扩展服务配置"
          desc="统一配置小红书打卡机位、高德地图 LBS 美食、天气雷达与 12306 高铁，支持官方 API Key 热加载免重启生效"
        />

        {/* 顶部指标卡片 */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex items-center gap-3.5 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-rose-50 text-rose-600">
              <Flame size={22} />
            </span>
            <div>
              <p className="text-xs text-slate-500">小红书爆款攻略</p>
              <p className="mt-0.5 text-sm font-bold text-slate-900">
                {xhsEnabled ? "🔥 优先检索出片机位" : "已停用"}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3.5 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
              <MapPin size={22} />
            </span>
            <div>
              <p className="text-xs text-slate-500">高德 LBS & 美食</p>
              <p className="mt-0.5 text-sm font-bold text-slate-900">
                {config?.services.amap.configured ? "已配置真实商户" : "待填入 Web Key"}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3.5 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600">
              <Train size={22} />
            </span>
            <div>
              <p className="text-xs text-slate-500">12306 大交通</p>
              <p className="mt-0.5 text-sm font-bold text-slate-900">
                {train12306Enabled ? "首末日高铁自动卡片" : "已关闭"}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3.5 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-amber-50 text-amber-600">
              <Rocket size={22} />
            </span>
            <div>
              <p className="text-xs text-slate-500">配置热生效架构</p>
              <p className="mt-0.5 text-sm font-bold text-emerald-700">
                免重启实时热加载
              </p>
            </div>
          </div>
        </div>

        {/* 提示消息 */}
        {saveSuccess && (
          <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            <CheckCircle2 size={18} />
            <span>{saveSuccess}</span>
          </div>
        )}
        {error && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
            <AlertCircle size={18} />
            <span>{error}</span>
          </div>
        )}

        {/* 测试反馈弹窗/条目 */}
        {testResult && (
          <div
            className={`flex items-center justify-between rounded-xl border px-4 py-3 text-sm ${
              testResult.ok
                ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                : "border-amber-200 bg-amber-50 text-amber-900"
            }`}
          >
            <div className="flex items-center gap-2">
              {testResult.ok ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
              <span>
                <strong>[{testResult.service}]</strong> {testResult.message}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setTestResult(null)}
              className="text-xs text-slate-500 hover:text-slate-800"
            >
              关闭
            </button>
          </div>
        )}

        {/* 服务配置卡片区域 */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* 1. 小红书文旅爆款攻略与打卡机位 */}
          <div className="flex flex-col justify-between rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-rose-50 text-rose-600">
                    <Camera size={18} />
                  </span>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900">
                      小红书文旅爆款攻略与打卡机位
                    </h3>
                    <p className="text-xs text-slate-500">
                      年轻人 Citywalk 路线、小众咖啡店与最佳拍照机位
                    </p>
                  </div>
                </div>
                <label className="relative inline-flex cursor-pointer items-center">
                  <input
                    type="checkbox"
                    className="peer sr-only"
                    checked={xhsEnabled}
                    onChange={(e) => setXhsEnabled(e.target.checked)}
                  />
                  <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-rose-500 peer-checked:after:translate-x-full"></div>
                </label>
              </div>

              <div className="mt-4 space-y-3">
                <div>
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-slate-700">
                      小红书 API Key / MCP 访问令牌
                    </label>
                    {config?.services.xiaohongshu.configured && (
                      <span className="text-[11px] text-emerald-600">
                        当前密钥：{config.services.xiaohongshu.key_preview}
                      </span>
                    )}
                  </div>
                  <input
                    type="password"
                    placeholder={
                      config?.services.xiaohongshu.configured
                        ? "输入新 Key 以覆盖，留空保持当前配置"
                        : "请粘贴小红书开放平台 API Key 或 MCP 授权 Token"
                    }
                    value={xhsKey}
                    onChange={(e) => setXhsKey(e.target.value)}
                    className="mt-1.5 w-full rounded-xl border border-slate-200 px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:border-rose-500 focus:outline-none"
                  />
                </div>

                <div className="rounded-xl bg-rose-50/60 p-3 text-[11px] leading-relaxed text-rose-950">
                  <p className="font-semibold text-rose-900">💡 开箱即用与热加载双轨机制：</p>
                  <p className="mt-0.5">
                    未配置 Key 时，系统自动启用<strong>本地精选爆款机位图谱</strong>（已内置上海、北京、成都、西安、杭州等经典热门机位与避坑锦囊）。您在上方填入官方 Key 并保存后，将<strong>免重启实时激活全网实时动态检索通道</strong>！
                  </p>
                </div>
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3">
              <a
                href="https://open.xiaohongshu.com/"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-rose-600 hover:underline"
              >
                <span>获取小红书开放平台 Key</span>
                <ExternalLink size={12} />
              </a>
              <button
                type="button"
                disabled={testingService === "xiaohongshu"}
                onClick={() => handleTest("xiaohongshu", xhsKey)}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                {testingService === "xiaohongshu" ? "正在探测…" : "测试连通性"}
              </button>
            </div>
          </div>

          {/* 2. 高德地图开放平台 */}
          <div className="flex flex-col justify-between rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                    <Utensils size={18} />
                  </span>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900">
                      高德地图开放平台 (LBS / POI / 周边美食 / 街景)
                    </h3>
                    <p className="text-xs text-slate-500">
                      方圆 1500m 真实 4.0+ 地道美食商户抓取与静态切图
                    </p>
                  </div>
                </div>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    config?.services.amap.configured
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-amber-50 text-amber-700"
                  }`}
                >
                  {config?.services.amap.configured ? "已配置" : "未配置"}
                </span>
              </div>

              <div className="mt-4 space-y-3">
                <div>
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-slate-700">
                      高德 Web 服务 Key (AMAP_KEY)
                    </label>
                    {config?.services.amap.configured && (
                      <span className="text-[11px] text-emerald-600">
                        当前密钥：{config.services.amap.key_preview}
                      </span>
                    )}
                  </div>
                  <input
                    type="password"
                    placeholder={
                      config?.services.amap.configured
                        ? "输入新 Key 以覆盖，留空保持当前配置"
                        : "请粘贴高德开放平台 Web 服务类型的 Key"
                    }
                    value={amapKey}
                    onChange={(e) => setAmapKey(e.target.value)}
                    className="mt-1.5 w-full rounded-xl border border-slate-200 px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:border-blue-500 focus:outline-none"
                  />
                </div>

                <div className="rounded-xl bg-blue-50/60 p-3 text-[11px] leading-relaxed text-blue-950">
                  <p className="font-semibold text-blue-900">💡 升级玩法联动：</p>
                  <p className="mt-0.5">
                    行程规划在排定午餐/晚餐时，静默调用高德商户检索，直接为卡片注入周边 500~1500 米地道名店与评分；并在时间轴卡片提供实景缩略图。
                  </p>
                </div>
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3">
              <a
                href="https://lbs.amap.com/api/webservice/summary"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
              >
                <span>前往高德控制台申请 Web 服务 Key</span>
                <ExternalLink size={12} />
              </a>
              <button
                type="button"
                disabled={testingService === "amap"}
                onClick={() => handleTest("amap", amapKey)}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                {testingService === "amap" ? "正在探测…" : "测试连通性"}
              </button>
            </div>
          </div>

          {/* 3. 和风天气 / 实时气象预报 */}
          <div className="flex flex-col justify-between rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-50 text-amber-600">
                    <CloudSun size={18} />
                  </span>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900">
                      和风天气 / 实时气象预报 (QWeather)
                    </h3>
                    <p className="text-xs text-slate-500">
                      每日穿衣建议、恶劣天气避险与降雨预警雷达
                    </p>
                  </div>
                </div>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    config?.services.weather.configured
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {config?.services.weather.configured ? "已配置" : "通用预报"}
                </span>
              </div>

              <div className="mt-4 space-y-3">
                <div>
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-slate-700">
                      和风天气 API Key (WEATHER_API_KEY)
                    </label>
                    {config?.services.weather.configured && (
                      <span className="text-[11px] text-emerald-600">
                        当前密钥：{config.services.weather.key_preview}
                      </span>
                    )}
                  </div>
                  <input
                    type="password"
                    placeholder="输入和风天气 Key 以激活分钟级降雨雷达"
                    value={weatherKey}
                    onChange={(e) => setWeatherKey(e.target.value)}
                    className="mt-1.5 w-full rounded-xl border border-slate-200 px-3.5 py-2 text-xs text-slate-800 placeholder-slate-400 focus:border-amber-500 focus:outline-none"
                  />
                </div>

                <div className="rounded-xl bg-amber-50/60 p-3 text-[11px] leading-relaxed text-amber-950">
                  <p className="font-semibold text-amber-900">🌤️ 动态天气横幅：</p>
                  <p className="mt-0.5">
                    已在时间轴每日行程顶部实时渲染气象横幅，并在遇强对流/暴雨天气时自动推荐就近室内博物馆或特色场馆。
                  </p>
                </div>
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3">
              <a
                href="https://dev.qweather.com/"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-amber-700 hover:underline"
              >
                <span>获取和风天气开发密钥</span>
                <ExternalLink size={12} />
              </a>
              <button
                type="button"
                disabled={testingService === "weather"}
                onClick={() => handleTest("weather", weatherKey)}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                {testingService === "weather" ? "正在探测…" : "测试连通性"}
              </button>
            </div>
          </div>

          {/* 4. 12306 高铁大交通与攻略优先策略 */}
          <div className="flex flex-col justify-between rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
                    <Train size={18} />
                  </span>
                  <div>
                    <h3 className="text-sm font-bold text-slate-900">
                      12306 动态高铁大交通 & 攻略优先策略
                    </h3>
                    <p className="text-xs text-slate-500">
                      首末日跨城高铁接驳卡片与文旅知识库权重
                    </p>
                  </div>
                </div>
                <label className="relative inline-flex cursor-pointer items-center">
                  <input
                    type="checkbox"
                    className="peer sr-only"
                    checked={train12306Enabled}
                    onChange={(e) => setTrain12306Enabled(e.target.checked)}
                  />
                  <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-indigo-500 peer-checked:after:translate-x-full"></div>
                </label>
              </div>

              <div className="mt-4 space-y-4">
                <div>
                  <label className="text-xs font-medium text-slate-700">
                    文旅攻略优先渠道（决定机位与避坑标签的默认选拔顺序）
                  </label>
                  <div className="mt-2 grid grid-cols-3 gap-2">
                    <button
                      type="button"
                      onClick={() => setPriority("xiaohongshu")}
                      className={`rounded-xl border p-2.5 text-center transition-all ${
                        priority === "xiaohongshu"
                          ? "border-rose-400 bg-rose-50 text-rose-800 font-semibold shadow-xs"
                          : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
                      }`}
                    >
                      <span className="block text-xs">📕 小红书爆款</span>
                      <span className="mt-0.5 block text-[9px] text-slate-400">
                        (出片/机位推荐)
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setPriority("zhihu")}
                      className={`rounded-xl border p-2.5 text-center transition-all ${
                        priority === "zhihu"
                          ? "border-blue-400 bg-blue-50 text-blue-800 font-semibold shadow-xs"
                          : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
                      }`}
                    >
                      <span className="block text-xs">知乎官方检索</span>
                      <span className="mt-0.5 block text-[9px] text-slate-400">
                        (深度经验长文)
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setPriority("local")}
                      className={`rounded-xl border p-2.5 text-center transition-all ${
                        priority === "local"
                          ? "border-emerald-400 bg-emerald-50 text-emerald-800 font-semibold shadow-xs"
                          : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
                      }`}
                    >
                      <span className="block text-xs">本地精选图谱</span>
                      <span className="mt-0.5 block text-[9px] text-slate-400">
                        (零网络极速)
                      </span>
                    </button>
                  </div>
                </div>

                <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3">
                  <div>
                    <span className="text-xs font-medium text-slate-800">
                      知乎文旅搜索 MCP 备选源
                    </span>
                    <p className="text-[10px] text-slate-500">
                      当小红书通道暂无匹配或停用时作为备选
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    checked={zhihuEnabled}
                    onChange={(e) => setZhihuEnabled(e.target.checked)}
                    className="h-4 w-4 rounded text-blue-600 focus:ring-blue-500"
                  />
                </div>
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3">
              <span className="text-xs text-slate-500">
                12306 动态列车服务已集成于平台内部网络
              </span>
              <button
                type="button"
                disabled={testingService === "train_12306"}
                onClick={() => handleTest("train_12306")}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                {testingService === "train_12306" ? "正在探测…" : "测试连通性"}
              </button>
            </div>
          </div>
        </div>

        {/* 底部保存按钮卡片 */}
        <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center gap-2 text-xs text-slate-600">
            <ShieldCheck size={16} className="text-emerald-600" />
            <span>
              所有填入的 Key 仅在服务器内部安全保管，不会暴露给前端浏览器或客户端，免重启即时热加载生效。
            </span>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={saving}
              onClick={() => void loadConfig()}
              className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              放弃更改
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => void handleSave()}
              className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? (
                <>
                  <RefreshCw size={14} className="animate-spin" />
                  <span>正在热加载保存…</span>
                </>
              ) : (
                <>
                  <Sparkles size={14} />
                  <span>保存并热生效 (免重启)</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </RoleGate>
  );
}
