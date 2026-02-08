import React from 'react';

const SCALE = 10;
const BACKGROUND_COLOR = '#FFFBF0';

const PALETTE = {
  '.': 'transparent',
  'h': '#aadd77',     // Highlight Green
  'g': '#7ecb47',     // Base Green
  'd': '#4d8b2c',     // Dark Green
  'o': '#2d5a1e',     // Outline
  'b': '#0f172a',     // Black (Eyes)
  'w': '#ffffff',     // White (Reflections)
  'u': '#3b82f6',     // Blue
  'l': '#bfdbfe',     // Light Blue
  'y': '#fbbf24',     // Gold
  'r': '#ef4444',     // Red
  'c': '#14b8a6',     // Teal
  'x': '#1e293b',     // Dark Grey
};

const MAP = [
  "........................",
  "....ooo..........ooo....", 
  "...o.l.o........o.u.o...",
  "...o.l.o........o.u.o...",
  "....ooo..........ooo....",
  ".....g............g.....",
  ".....g............g.....",
  "...oooooooooooooooooo...",
  "..ogghhhhhhhhhhhhhgggo..",
  ".ogghhhhhhhhhhhhhhhgggo.",
  ".ogbbbbbhhhhhhhhbbbbbgo.", 
  "oghbbwwwbbhhhhbbwwwbbhgo", 
  "oghbbwwwbbhhhhbbwwwbbhgo", 
  "oghbbwwwbbhhhhbbwwwbbhgo", 
  "oggbbbbbbbhhhhbbbbbbbggo", 
  "ogghbbbbbhhhhhhbbbbbhggo", 
  "oggggghhhhhhhhhhhhgggggo", 
  "oggggghhhhhbbhhhhhgggggo", 
  ".oggggggghhbbhhgggggggo.", 
  "..ogggggggggggggggggoo..",
  "...ooooooooooooooooo....",
  "........................",
  "........................",
  "........................",
];

const PixelRender = ({ map, palette, scale }) => {
  const size = map.length;
  return (
    <svg 
      width={size * scale} 
      height={size * scale} 
      viewBox={`0 0 ${size} ${size}`} 
      style={{ 
        imageRendering: 'pixelated', 
        shapeRendering: 'crispEdges',
        filter: 'drop-shadow(0px 8px 12px rgba(0,0,0,0.1))'
      }}
    >
      {map.map((row, y) => {
        if (!row) return null;
        return row.split('').map((char, x) => {
          if (char === '.' || !palette[char]) return null;
          return <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={palette[char]} />;
        })
      })}
    </svg>
  );
};

export default function App() {
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: BACKGROUND_COLOR }}>
      <PixelRender map={MAP} palette={PALETTE} scale={SCALE} />
    </div>
  );
}