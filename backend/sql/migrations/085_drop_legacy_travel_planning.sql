-- 独立旅游 V2 数据层已接管规划保存；只删除明确废弃的三张规划表。
-- RESTRICT 是安全边界：存在其他模块依赖时迁移必须失败，禁止 CASCADE。
DROP TABLE IF EXISTS public.travel_decision_audit RESTRICT;
DROP TABLE IF EXISTS public.travel_plan_versions RESTRICT;
DROP TABLE IF EXISTS public.travel_preferences RESTRICT;
