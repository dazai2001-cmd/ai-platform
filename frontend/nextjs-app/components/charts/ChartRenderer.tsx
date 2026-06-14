"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const COLORS = ["#22d3ee", "#818cf8", "#34d399", "#fbbf24", "#f472b6"];

interface ChartSpec {
  chart_type: "bar" | "line" | "pie" | "scatter";
  title: string;
  x_label: string;
  y_label: string;
  data: {
    labels: string[];
    series?: { name: string; values: number[] }[];
    values?: number[];
  };
}

export default function ChartRenderer({ chart, compact = false }: { chart: ChartSpec; compact?: boolean }) {
  const labels = chart.data?.labels || [];
  const series = chart.data?.series?.length
    ? chart.data.series
    : chart.data?.values
      ? [{ name: chart.y_label || "Value", values: chart.data.values }]
      : [];
  const data = labels.map((label, i) => ({
    name: label,
    ...Object.fromEntries(series.map((item) => [item.name, item.values[i] ?? 0])),
  }));
  const height = compact ? 180 : 280;

  return (
    <div className="mt-4 rounded-md border border-slate-800 bg-slate-950/80 p-4">
      <p className="mb-4 truncate text-sm font-medium text-slate-200">{chart.title}</p>
      <ResponsiveContainer width="100%" height={height}>
        {chart.chart_type === "bar" ? (
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <Tooltip contentStyle={{ backgroundColor: "#020617", border: "1px solid #334155", borderRadius: 6 }} />
            {!compact && <Legend />}
            {series.map((item, index) => (
              <Bar key={item.name} dataKey={item.name} fill={COLORS[index % COLORS.length]} radius={[3, 3, 0, 0]} />
            ))}
          </BarChart>
        ) : chart.chart_type === "line" ? (
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <Tooltip contentStyle={{ backgroundColor: "#020617", border: "1px solid #334155", borderRadius: 6 }} />
            {!compact && <Legend />}
            {series.map((item, index) => (
              <Line
                key={item.name}
                type="monotone"
                dataKey={item.name}
                stroke={COLORS[index % COLORS.length]}
                strokeWidth={2}
                dot={false}
              />
            ))}
          </LineChart>
        ) : chart.chart_type === "pie" ? (
          <PieChart>
            <Pie data={data} dataKey={series[0]?.name} nameKey="name" cx="50%" cy="50%" outerRadius={compact ? 54 : 96} label={!compact}>
              {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
            </Pie>
            <Tooltip contentStyle={{ backgroundColor: "#020617", border: "1px solid #334155", borderRadius: 6 }} />
            {!compact && <Legend />}
          </PieChart>
        ) : (
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <YAxis dataKey={series[0]?.name} tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <Tooltip contentStyle={{ backgroundColor: "#020617", border: "1px solid #334155", borderRadius: 6 }} />
            <Scatter data={data} dataKey={series[0]?.name} fill="#22d3ee" />
          </ScatterChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
