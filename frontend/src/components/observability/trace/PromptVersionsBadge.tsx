"use client";

interface PromptVersionEntry {
  key: string;
  version: number | null;
  source: string;
}

interface Props {
  metadata: Record<string, unknown>;
}

export default function PromptVersionsBadge({ metadata }: Props) {
  const versions = (metadata?.prompt_versions as PromptVersionEntry[] | undefined) ?? [];

  if (versions.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5">
      {versions.map((v) => (
        <span
          key={v.key}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono bg-indigo-50 text-indigo-700 border border-indigo-200"
          title={`来源: ${v.source}`}
        >
          {v.key}
          <span className="text-indigo-400">@</span>
          {v.version != null ? `v${v.version}` : "default"}
        </span>
      ))}
    </div>
  );
}
