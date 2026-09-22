-- 039: llm_models.model_kind 放开 'ocr' 用途（2026-09-22）。
--
-- OCR 角色长期存在（rag/preprocessing/parser/ocr.py 按 model_roles 解析），
-- 但模型用途枚举一直没有 'ocr'，用户登记 OCR 模型只能选 vision/chat，
-- 永远无法绑定到 OCR 角色（期望 kind='ocr'）。幂等：先 DROP 再 ADD。
ALTER TABLE llm_models DROP CONSTRAINT IF EXISTS llm_models_model_kind_check;
ALTER TABLE llm_models ADD CONSTRAINT llm_models_model_kind_check
    CHECK (model_kind = ANY (ARRAY['chat'::text, 'embedding'::text, 'rerank'::text,
                                  'vision'::text, 'speech'::text, 'ocr'::text]));
