import { ApiError } from '../../services/api';

/** Report categories accepted by the API (Req 23.1). */
export const REPORT_CATEGORIES = [
  'blocked road',
  'flooding',
  'fallen debris',
  'fire or smoke',
  'blocked building exit',
  'inaccessible stairwell',
  'transit closure',
  'person needing assistance',
  'other',
] as const;

export const MAX_DESCRIPTION = 1000;
export const MAX_MESSAGE = 1000;

export const MESSAGE_TYPES = [
  { value: 'update', label: 'Update' },
  { value: 'observation', label: 'Observation' },
  { value: 'assistance_request', label: 'Assistance request' },
  { value: 'person_safe', label: 'Person is OK' },
  { value: 'accessibility_assistance_request', label: 'Accessibility assistance request' },
  { value: 'blocked_route_update', label: 'Blocked route update' },
] as const;

export const ASSISTANCE_MESSAGE_TYPES: readonly string[] = ['assistance_request', 'accessibility_assistance_request'];

export const CHECKIN_STATUSES = [
  { value: 'safe', label: "I'm safe" },
  { value: 'need_assistance', label: 'I need assistance' },
  { value: 'cannot_reach_destination', label: 'I cannot reach my planned destination' },
  { value: 'route_blocked', label: 'My route is blocked' },
] as const;

export type VerificationStatusValue =
  | 'Unverified'
  | 'Corroborated'
  | 'Officially_Verified'
  | 'Disputed'
  | 'Resolved'
  | 'Expired';

export interface CommunityReport {
  report_id: string;
  category: string;
  description: string;
  created_at: string | null;
  alias: string | null;
  source_label: string;
  verification_status: VerificationStatusValue | string;
  confirmations: number;
  disputes: number;
  simulated: boolean;
}

export interface CommunityMessage {
  message_id: string;
  message_type: string;
  content: string;
  created_at: string | null;
  alias: string | null;
  is_official: boolean;
  source_record_id: string | null;
  source_organization: string | null;
  source_url: string | null;
}

export interface OfficialNoticeItem {
  notice_id: string;
  text: string;
  source_organization: string | null;
  source_url: string | null;
  source_record_id: string | null;
  is_official: boolean;
  simulated: boolean;
  published_at: string | null;
}

export interface CommunityCheckIn {
  checkin_id: string;
  status: string;
  status_label: string | null;
  created_at: string | null;
  alias: string | null;
}

type Raw = Record<string, unknown>;
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

export function toReport(e: Raw): CommunityReport | null {
  const id = str(e.report_id) ?? str(e.id);
  if (!id) return null;
  return {
    report_id: id,
    category: str(e.category) ?? 'other',
    description: str(e.description) ?? '',
    created_at: str(e.created_at),
    alias: str(e.alias),
    source_label: str(e.source_label) ?? 'Community report',
    verification_status: str(e.verification_status) ?? 'Unverified',
    confirmations: num(e.confirmations),
    disputes: num(e.disputes),
    simulated: e.simulated === true,
  };
}

export function toMessage(e: Raw): CommunityMessage | null {
  const id = str(e.message_id) ?? str(e.id);
  if (!id || e.hidden === true) return null;
  const rec = (e.source_record ?? {}) as Raw;
  return {
    message_id: id,
    message_type: str(e.message_type) ?? 'update',
    content: str(e.content) ?? '',
    created_at: str(e.created_at),
    alias: str(e.alias),
    is_official: e.is_official === true || rec.is_official === true,
    source_record_id: str(e.source_record_id) ?? str(rec.source_id),
    source_organization: str(e.source_organization),
    source_url: str(e.source_url),
  };
}

export function toCheckIn(e: Raw): CommunityCheckIn | null {
  const id = str(e.checkin_id) ?? str(e.check_in_id) ?? str(e.id);
  if (!id) return null;
  return {
    checkin_id: id,
    status: str(e.status) ?? 'safe',
    status_label: str(e.status_label),
    created_at: str(e.created_at),
    alias: str(e.alias),
  };
}

export function toNotice(m: CommunityMessage): OfficialNoticeItem {
  return {
    notice_id: m.message_id,
    text: m.content,
    source_organization: m.source_organization,
    source_url: m.source_url,
    source_record_id: m.source_record_id,
    is_official: m.is_official,
    simulated: false,
    published_at: m.created_at,
  };
}

export interface SubmitFailure {
  /** field name -> problem text from the API error envelope. */
  fields: Record<string, string>;
  message: string;
  network: boolean;
}

/** Turn a thrown error into field errors + a summary message (Req 23.7). */
export function toFailure(err: unknown): SubmitFailure {
  if (err instanceof ApiError) {
    const fields: Record<string, string> = {};
    for (const f of err.fields) fields[f.field] = f.problem;
    return { fields, message: err.message || 'The request failed.', network: false };
  }
  const network = err instanceof TypeError;
  return {
    fields: {},
    message: network ? 'Could not reach SafePath. Check your connection.' : 'Something went wrong. Try again.',
    network,
  };
}

export function formatTime(iso: string | null): string {
  if (!iso) return 'Unknown time';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? 'Unknown time' : d.toLocaleString();
}
