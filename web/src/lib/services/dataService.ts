// Service 层 — 数据接入 + 处理 + 资产

import * as api from '@/lib/api-data'
import type { UploadResult, DatasetInfo, PipelineJob, DataAsset } from '@/lib/api-data'

export const dataService = {
  async upload(file: File): Promise<UploadResult> {
    return api.uploadFile(file)
  },

  async getDatasets(): Promise<DatasetInfo[]> {
    return api.listDatasets()
  },

  async generateData(types: string[], count: number) {
    return api.generateData(types, count)
  },

  async runPipeline(fileId: string): Promise<PipelineJob | null> {
    const res = await api.runPipeline(fileId)
    return res.job || null
  },

  async getPipelineHistory(): Promise<PipelineJob[]> {
    return api.pipelineHistory()
  },

  async getAssets(): Promise<DataAsset[]> {
    return api.listAssets()
  },

  // ── Data Collection Center ──
  async triggerCollect(dataset: string, enableWrite: boolean = false) {
    return api.triggerCollect(dataset, enableWrite)
  },
  async triggerCollectAll(enableWrite: boolean = false) {
    return api.triggerCollectAll(enableWrite)
  },
  async collectHistory(limit: number = 20) {
    return api.collectHistory(limit)
  },
}
