import React from 'react';

function FrequencyDeviationPlot({ data }) {
  const { times = [], deviations = [] } = data || {};
  if (!times.length) {
    return <div className="chart-placeholder">No deviation data</div>;
  }
  const width = 260;
  const height = 100;
  const maxAbs = Math.max(...deviations.map((d) => Math.abs(d)), 1);
  const points = times
    .map((t, i) => {
      const x = (i / (times.length - 1)) * width;
      const y = height / 2 - (deviations[i] / maxAbs) * (height / 2);
      return `${x},${y}`;
    })
    .join(' ');
  return (
    <svg width={width} height={height} className="line-chart">
      <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="gray" />
      <polyline fill="none" stroke="purple" strokeWidth="2" points={points} />
    </svg>
  );
}

export default FrequencyDeviationPlot;
