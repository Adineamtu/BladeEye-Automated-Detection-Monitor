import React from 'react';

function ConstellationPlot({ samples, showDecisionBoundaries = false }) {
  const width = 260;
  const height = 260;

  if (!samples.length) return <div className="chart-placeholder">No I/Q data</div>;

  const max = Math.max(
    1,
    ...samples.flatMap(([i, q]) => [Math.abs(i), Math.abs(q)])
  );

  const points = samples.map(([i, q], idx) => {
    const x = (i / max) * (width / 2) + width / 2;
    const y = height / 2 - (q / max) * (height / 2);
    return (
      <circle
        key={idx}
        data-testid="iq-point"
        cx={x}
        cy={y}
        r={2}
        fill="green"
      />
    );
  });

  return (
    <svg
      data-testid="constellation-plot"
      width={width}
      height={height}
      className="constellation-plot"
    >
      <rect x="0" y="0" width={width} height={height} fill="none" stroke="black" />
      <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="gray" />
      <line x1={width / 2} y1="0" x2={width / 2} y2={height} stroke="gray" />
      {showDecisionBoundaries && (
        <>
          <line
            x1="0"
            y1={height / 2}
            x2={width}
            y2={height / 2}
            stroke="red"
            strokeDasharray="4 2"
          />
          <line
            x1={width / 2}
            y1="0"
            x2={width / 2}
            y2={height}
            stroke="red"
            strokeDasharray="4 2"
          />
        </>
      )}
      {points}
      <text x={width - 10} y={height / 2 - 5} fontSize="10">
        I
      </text>
      <text x={width / 2 + 5} y={10} fontSize="10">
        Q
      </text>
    </svg>
  );
}

export default ConstellationPlot;
