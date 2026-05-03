/**
 * Tiny pure-SVG sparkline. We deliberately don't pull in a chart
 * library — for a 15-bucket velocity chart, hand-drawn SVG is
 * smaller than the loader for any of recharts/visx/echarts.
 */

import type { VelocityPoint } from '@/lib/types';

export function VelocitySparkline({
  points,
  height = 80,
}: {
  points: VelocityPoint[];
  height?: number;
}) {
  if (points.length === 0) return null;

  const w = 600;
  const padX = 8;
  const padY = 8;
  const innerW = w - padX * 2;
  const innerH = height - padY * 2;
  const max = Math.max(1, ...points.map((p) => p.apps));
  const stepX = innerW / Math.max(1, points.length - 1);

  const okPath = points
    .map((p, i) => {
      const x = padX + i * stepX;
      const y = padY + innerH - (p.ok / max) * innerH;
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');

  const failedPath = points
    .map((p, i) => {
      const x = padX + i * stepX;
      const y = padY + innerH - (p.failed / max) * innerH;
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');

  const totalApps = points.reduce((acc, p) => acc + p.apps, 0);
  const totalOk = points.reduce((acc, p) => acc + p.ok, 0);
  const totalFailed = points.reduce((acc, p) => acc + p.failed, 0);

  return (
    <div className="card">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted">
          Velocity (last {points.length} days)
        </h3>
        <span className="text-xs text-muted">
          <span className="text-ok">{totalOk} ok</span> ·{' '}
          <span className="text-err">{totalFailed} failed</span> · {totalApps}{' '}
          total
        </span>
      </div>
      <svg
        viewBox={`0 0 ${w} ${height}`}
        className="h-20 w-full"
        preserveAspectRatio="none"
      >
        {/* baseline */}
        <line
          x1={padX}
          y1={height - padY}
          x2={w - padX}
          y2={height - padY}
          stroke="#1f1f24"
          strokeWidth={1}
        />
        {/* bars: total apps */}
        {points.map((p, i) => {
          const x = padX + i * stepX - 2;
          const h = (p.apps / max) * innerH;
          const y = padY + innerH - h;
          return (
            <rect
              key={i}
              x={x}
              y={y}
              width={4}
              height={h}
              fill="#7c5cff"
              opacity={0.4}
            />
          );
        })}
        <path d={okPath} fill="none" stroke="#10b981" strokeWidth={1.5} />
        <path d={failedPath} fill="none" stroke="#ef4444" strokeWidth={1.5} />
      </svg>
    </div>
  );
}
