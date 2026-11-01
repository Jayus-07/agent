/**
 * BookingConfirmGate — 预订确认门 UI（收编重构 §4.2 / 原型 B7）
 *
 * 两段式：报价（quote，可取消）→ 确认卡（金额/币种/截止时间）→ 显式确认
 * → 状态。绝不自动提交确认；TTL 过期/价格变化（new_quote）如实呈现。
 */
import { useCallback, useState } from 'react';

import {
  confirmTravelBookingV2,
  createTravelBookingQuoteV2,
  fetchTravelBookingStatusV2,
  type TravelBookingConfirmResultV2,
  type TravelBookingQuoteResultV2,
  type TravelBookingStatusResultV2,
} from '../../../api/travelV2';

export interface BookingConfirmGateProps {
  tripId: string;
  commerceType: 'hotel' | 'flight';
  searchParams: Record<string, unknown>;
  selection?: Record<string, unknown>;
  label?: string;
}

type Phase =
  | { kind: 'idle' }
  | { kind: 'quoting' }
  | { kind: 'quote'; quote: TravelBookingQuoteResultV2; submitting: boolean }
  | { kind: 'done'; result: TravelBookingConfirmResultV2 }
  | { kind: 'status'; status: TravelBookingStatusResultV2 }
  | { kind: 'error'; message: string };

function extractDetail(value: unknown): string {
  if (value && typeof value === 'object' && 'detail' in (value as Record<string, unknown>)) {
    const detail = (value as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'message' in detail) {
      const msg = (detail as { message?: unknown }).message;
      if (typeof msg === 'string') return msg;
    }
  }
  return '预订请求失败，请稍后再试';
}

export function BookingConfirmGate(props: BookingConfirmGateProps) {
  const { tripId, commerceType, searchParams, selection, label } = props;
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });

  const startQuote = useCallback(async () => {
    setPhase({ kind: 'quoting' });
    try {
      const quote = await createTravelBookingQuoteV2(tripId, {
        commerce_type: commerceType,
        search_params: searchParams,
        selection,
      });
      setPhase({ kind: 'quote', quote, submitting: false });
    } catch (err) {
      setPhase({ kind: 'error', message: extractDetail(err) });
    }
  }, [tripId, commerceType, searchParams, selection]);

  const confirm = useCallback(async (bookingIntentId: string) => {
    setPhase((prev) => (
      prev.kind === 'quote' ? { ...prev, submitting: true } : prev
    ));
    try {
      const result = await confirmTravelBookingV2(tripId, bookingIntentId);
      setPhase({ kind: 'done', result });
    } catch (err) {
      setPhase({ kind: 'error', message: extractDetail(err) });
    }
  }, [tripId]);

  const refreshStatus = useCallback(async (bookingIntentId: string) => {
    try {
      const status = await fetchTravelBookingStatusV2(tripId, bookingIntentId);
      setPhase({ kind: 'status', status });
    } catch (err) {
      setPhase({ kind: 'error', message: extractDetail(err) });
    }
  }, [tripId]);

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm" data-testid="booking-confirm-gate">
      <div className="mb-1 font-medium text-amber-800">
        {label ?? '预订确认门'}
      </div>

      {(phase.kind === 'idle' || phase.kind === 'error') && (
        <button
          type="button"
          className="rounded bg-amber-600 px-3 py-1 text-white hover:bg-amber-700"
          data-testid="booking-start"
          onClick={startQuote}
        >
          创建预订报价
        </button>
      )}
      {phase.kind === 'error' && (
        <p className="mt-2 text-red-600" data-testid="booking-error">{phase.message}</p>
      )}

      {phase.kind === 'quoting' && (
        <p data-testid="booking-quoting">正在获取报价…</p>
      )}

      {phase.kind === 'quote' && (
        <div data-testid="booking-quote-card">
          <p>
            金额：<strong>{phase.quote.amount} {phase.quote.currency}</strong>
            {phase.quote.internal_expires_at && (
              <span className="ml-2 text-amber-700">
                确认截止：{phase.quote.internal_expires_at}
              </span>
            )}
          </p>
          <p className="mt-1 text-xs text-stone-600">{phase.quote.message}</p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={phase.submitting}
              className="rounded bg-emerald-600 px-3 py-1 text-white hover:bg-emerald-700 disabled:opacity-50"
              data-testid="booking-confirm"
              onClick={() => confirm(phase.quote.booking_intent_id)}
            >
              {phase.submitting ? '提交中…' : '确认预订'}
            </button>
            <button
              type="button"
              className="rounded border px-3 py-1"
              data-testid="booking-cancel"
              onClick={() => setPhase({ kind: 'idle' })}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'done' && (
        <div data-testid="booking-done">
          <p>
            预订结果：<strong>{phase.result.result}</strong>
          </p>
          <p className="mt-1 text-xs text-stone-600">{phase.result.message}</p>
          <button
            type="button"
            className="mt-2 rounded border px-3 py-1"
            data-testid="booking-refresh-status"
            onClick={() => refreshStatus(phase.result.booking_intent_id)}
          >
            查询订单状态
          </button>
        </div>
      )}

      {phase.kind === 'status' && (
        <div data-testid="booking-status">
          <p>订单状态：{phase.status.status}</p>
          <p className="mt-1 text-xs text-stone-600">{phase.status.message}</p>
        </div>
      )}
    </div>
  );
}

export default BookingConfirmGate;
