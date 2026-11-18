// Live speed of every network connection: a stacked area chart of the last
// minute, plus one "lane" per connection whose flow runs faster as it carries
// more of the download.

import { Cable, Globe, RefreshCw, Smartphone, Wifi } from "lucide-react";
import { useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";

import { Button, Card, Switch } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatBytes, formatSpeed } from "@/lib/format";
import {
  DEFAULT_PATH,
  WINDOW,
  useNetwork,
  useNetworkActions,
  useSpeedSeries,
  type AdapterKind,
  type NetworkAdapter,
} from "@/lib/netspeed";

const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4"];
const color = (slot: number) => `rgb(var(${SERIES[slot % SERIES.length]}))`;

const KIND_ICON: Record<AdapterKind | "default", ReactNode> = {
  wifi: <Wifi className="h-3.5 w-3.5" />,
  ethernet: <Cable className="h-3.5 w-3.5" />,
  cellular: <Smartphone className="h-3.5 w-3.5" />,
  other: <Globe className="h-3.5 w-3.5" />,
  default: <Globe className="h-3.5 w-3.5" />,
};

const KIND_LABEL: Record<AdapterKind, string> = {
  wifi: "Wi-Fi",
  ethernet: "Ethernet",
  cellular: "Phone or mobile",
  other: "Network",
};

interface Path {
  key: string;
  label: string;
  detail: string;
  kind: AdapterKind | "default";
  slot: number;
  values: number[];
  total: number;
}

const W = 600;
const H = 140;

/** Stacked areas: each series sits on the ones before it. */
function stack(paths: Path[]): { top: number[]; base: number[] }[] {
  const running = new Array<number>(WINDOW).fill(0);
  return paths.map((path) => {
    const base = running.slice();
    path.values.forEach((value, i) => (running[i] += value));
    return { base, top: running.slice() };
  });
}

function niceMax(value: number): number {
  if (value <= 0) return 1024 * 1024; // an idle chart shows a 1 MB/s scale
  const exp = 1024 ** Math.floor(Math.log(value) / Math.log(1024));
  const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
  const unit = value / exp;
  return (steps.find((step) => step >= unit * 1.1) ?? 1024) * exp;
}

function areaPath(top: number[], base: number[], max: number): string {
  const x = (i: number) => (i / (WINDOW - 1)) * W;
  const y = (v: number) => H - (v / max) * H;
  const upper = top.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const lower = base
    .map((v, i) => [x(i), y(v)] as const)
    .reverse()
    .map(([px, py]) => `L${px.toFixed(1)},${py.toFixed(1)}`)
    .join("");
  return `${upper}${lower}Z`;
}

function linePath(top: number[], max: number): string {
  return top
    .map((v, i) => `${i ? "L" : "M"}${((i / (WINDOW - 1)) * W).toFixed(1)},${(H - (v / max) * H).toFixed(1)}`)
    .join("");
}

function SpeedChart({ paths }: { paths: Path[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const layers = useMemo(() => stack(paths), [paths]);
  const peak = Math.max(0, ...(layers.at(-1)?.top ?? [0]));
  const max = niceMax(peak);

  const onMove = (event: PointerEvent<HTMLDivElement>) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || box.width === 0) return;
    const fraction = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    setHover(Math.round(fraction * (WINDOW - 1)));
  };

  const hoverLeft = hover === null ? 0 : (hover / (WINDOW - 1)) * 100;
  return (
    <div className="relative">
      <div className="pointer-events-none absolute left-0 top-0 text-2xs text-subtle">
        <span className="tabular">{formatSpeed(max)}</span>
      </div>
      <div className="pointer-events-none absolute left-0 top-[74px] text-2xs text-subtle">
        <span className="tabular">{formatSpeed(max / 2)}</span>
      </div>
      <div
        ref={ref}
        className="relative mt-4 h-[140px] touch-none"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
        role="img"
        aria-label={`Download speed over the last ${WINDOW} seconds`}
      >
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible">
          {[0.5, 1].map((f) => (
            <line key={f} x1={0} x2={W} y1={H - f * H} y2={H - f * H} stroke="rgb(var(--line))" strokeWidth={1} vectorEffect="non-scaling-stroke" strokeDasharray={f === 1 ? "3 3" : undefined} />
          ))}
          <line x1={0} x2={W} y1={H} y2={H} stroke="rgb(var(--line-strong))" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          {paths.map((path, index) => (
            <g key={path.key}>
              <path d={areaPath(layers[index].top, layers[index].base, max)} fill={color(path.slot)} fillOpacity={0.22} />
              <path d={linePath(layers[index].top, max)} fill="none" stroke={color(path.slot)} strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            </g>
          ))}
          {hover !== null && (
            <line x1={(hover / (WINDOW - 1)) * W} x2={(hover / (WINDOW - 1)) * W} y1={0} y2={H} stroke="rgb(var(--muted))" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {hover !== null && (
          <div
            className="pointer-events-none absolute top-1 z-10 min-w-[150px] rounded-md border border-line-strong bg-overlay px-2.5 py-2 text-xs shadow-lg"
            style={hoverLeft > 55 ? { right: `${100 - hoverLeft + 2}%` } : { left: `${hoverLeft + 2}%` }}
          >
            <p className="mb-1 text-2xs text-subtle">{WINDOW - 1 - hover === 0 ? "Now" : `${WINDOW - 1 - hover} s ago`}</p>
            {paths.map((path) => (
              <div key={path.key} className="flex items-center gap-2 py-0.5">
                <span className="h-0.5 w-3 rounded-full" style={{ background: color(path.slot) }} />
                <span className="tabular font-semibold text-fg">{formatSpeed(path.values[hover]) === "—" ? "0 B/s" : formatSpeed(path.values[hover])}</span>
                <span className="truncate text-muted">{path.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="mt-1 flex justify-between text-2xs text-subtle">
        <span>{WINDOW} s ago</span>
        <span>now</span>
      </div>
    </div>
  );
}

/** One connection: name, speed, share, and a flow that speeds up with traffic. */
function Lane({ path, share }: { path: Path; share: number }) {
  const speed = path.values.at(-1) ?? 0;
  const moving = speed > 1024;
  // 2.4 s per cycle when barely moving, down to 0.25 s at ~50 MB/s and up.
  const duration = moving ? Math.max(0.25, 2.4 - Math.log10(speed / 1024) * 0.45) : 0;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line bg-raised" style={{ color: color(path.slot) }}>
          {KIND_ICON[path.kind]}
        </span>
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-fg">{path.label}</p>
          <p className="truncate text-2xs text-subtle">{path.detail}</p>
        </div>
      </div>
      <div className="text-right">
        <p className="tabular text-[13px] font-semibold text-fg">{moving ? formatSpeed(speed) : "Idle"}</p>
        <p className="tabular text-2xs text-subtle">{Math.round(share * 100)}% · {formatBytes(path.total)}</p>
      </div>
      <svg className="col-span-2 h-2 w-full" viewBox="0 0 300 8" preserveAspectRatio="none" aria-hidden>
        <line x1={0} x2={300} y1={4} y2={4} stroke="rgb(var(--line))" strokeWidth={2} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        <line x1={0} x2={Math.max(2, share * 300)} y1={4} y2={4} stroke={color(path.slot)} strokeOpacity={0.3} strokeWidth={4} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        {moving && (
          <line
            className="net-flow"
            x1={0}
            x2={Math.max(12, share * 300)}
            y1={4}
            y2={4}
            stroke={color(path.slot)}
            strokeWidth={4}
            strokeLinecap="round"
            strokeDasharray="4 8"
            vectorEffect="non-scaling-stroke"
            style={{ animationDuration: `${duration}s` }}
          />
        )}
      </svg>
    </li>
  );
}

function describe(adapter: NetworkAdapter): string {
  const parts = [KIND_LABEL[adapter.kind], adapter.address];
  if (adapter.online === false) parts.push("no internet");
  else if (adapter.latency_ms !== null) parts.push(`${Math.round(adapter.latency_ms)} ms`);
  return parts.join(" · ");
}

export function NetworkPanel() {
  const network = useNetwork();
  const series = useSpeedSeries();
  const { refresh, toggle } = useNetworkActions();
  const snapshot = network.data;

  const paths = useMemo<Path[]>(() => {
    if (!snapshot) return [];
    const zero = new Array<number>(WINDOW).fill(0);
    const list: Path[] = [];
    const multi = snapshot.multi_path_active;
    snapshot.adapters.forEach((adapter, index) => {
      const values = series.get(adapter.id) ?? zero;
      const used = adapter.total_bytes > 0 || values.some((v) => v > 0);
      if (!(multi ? adapter.active : false) && !used) return;
      list.push({
        key: adapter.id,
        label: adapter.name,
        detail: describe(adapter),
        kind: adapter.kind,
        slot: index + 1,
        values,
        total: adapter.total_bytes,
      });
    });
    const usual = snapshot.adapters.find((adapter) => adapter.online !== false);
    list.unshift({
      key: DEFAULT_PATH,
      label: multi ? "Other traffic" : "Usual connection",
      detail: multi ? "Sign-ins and links, over the usual route" : usual ? describe(usual) : "Windows picks the route",
      kind: multi ? "default" : usual?.kind ?? "default",
      slot: 0,
      values: series.get(DEFAULT_PATH) ?? zero,
      total: snapshot.default.total_bytes,
    });
    return list;
  }, [series, snapshot]);

  const now = paths.reduce((sum, path) => sum + (path.values.at(-1) ?? 0), 0);
  const usable = snapshot?.adapters.filter((adapter) => adapter.online !== false) ?? [];

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div>
          <p className="text-xs text-muted">Network speed</p>
          <p className="tabular mt-1 text-2xl font-semibold tracking-tight text-fg">{now > 1024 ? formatSpeed(now) : "Idle"}</p>
          <p className="mt-0.5 text-2xs text-subtle">
            {snapshot?.multi_path_active
              ? `Downloading over ${paths.length - 1} connections at once`
              : snapshot?.enabled
                ? "Waiting for a second connection that reaches the internet"
                : "One connection"}
          </p>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-start justify-end gap-3 sm:flex-none">
          <div className="w-full max-w-[320px]">
            <Switch
              label="Use every connection at once"
              description={
                usable.length >= 2
                  ? `Splits each download over ${usable.map((a) => a.name).join(" and ")}.`
                  : "Connect a second Wi-Fi adapter, Ethernet or a phone to download over both."
              }
              checked={snapshot?.enabled ?? false}
              disabled={!snapshot || toggle.isPending}
              onChange={(value) => toggle.mutate(value)}
            />
          </div>
          <Button variant="ghost" size="sm" icon={<RefreshCw className={cn("h-3.5 w-3.5", refresh.isPending && "animate-spin")} />} onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            Check connections
          </Button>
        </div>
      </div>

      {network.isError ? (
        <p className="mt-4 text-xs text-subtle">Network speeds are not available.</p>
      ) : (
        <div className="mt-3 grid gap-x-6 gap-y-2 lg:grid-cols-[minmax(0,1fr)_320px]">
          <SpeedChart paths={paths} />
          <ul className="divide-y divide-line self-center" aria-label="Connections">
            {paths.map((path) => (
              <Lane key={path.key} path={path} share={now > 0 ? (path.values.at(-1) ?? 0) / now : 0} />
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
