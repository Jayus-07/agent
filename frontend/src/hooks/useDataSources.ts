'use client'

import { useState, useEffect, useCallback } from 'react'
import { dataService } from '@/lib/services/dataService'
import type { UploadResult, DatasetInfo } from '@/lib/api-data'

export function useDataSources() {
  const [datasets, setDatasets] = useState<DatasetInfo[]>([])
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)
  const [uploading, setUploading] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    dataService.getDatasets().then(setDatasets).finally(() => setLoading(false))
  }, [])

  const upload = useCallback(async (file: File) => {
    setUploading(true)
    const res = await dataService.upload(file)
    setUploadResult(res)
    setUploading(false)
    return res
  }, [])

  return { datasets, uploadResult, uploading, upload, loading }
}
