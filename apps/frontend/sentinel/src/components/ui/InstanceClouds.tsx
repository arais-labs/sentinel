import { useEffect, useRef } from 'react';

// The original five-octave, warped density field, now evaluated on the GPU.
const fragmentSource = `
precision highp float;
uniform sampler2D grid;
uniform vec2 resolution;
uniform float aspect, time;
uniform float ring, energy;
uniform vec3 color;
float noise(vec2 p) {
  vec2 c = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = texture2D(grid, (c + vec2(.5,.5))/256.0).r;
  float b = texture2D(grid, (c + vec2(1.5,.5))/256.0).r;
  float d = texture2D(grid, (c + vec2(.5,1.5))/256.0).r;
  float e = texture2D(grid, (c + vec2(1.5,1.5))/256.0).r;
  return mix(mix(a,b,f.x),mix(d,e,f.x),f.y);
}
void main() {
  vec2 uv = (gl_FragCoord.xy-.5)/(resolution-1.0);
  uv.y = 1.0-uv.y;
  vec2 p = uv * vec2(aspect,1.0)*2.6;
  vec2 center = (uv-.5)*vec2(aspect,1.0);
  if (ring > .5) {
    // A Cartesian density field has no angular seam or rotational pull.
    p = center*7.0;
  }
  vec2 temporal = vec2(
    sin(time*.47)*.28 + sin(time*1.13+2.1)*.12,
    cos(time*.59+1.3)*.25 + cos(time*.91-.6)*.14);
  vec2 warp = vec2(noise(p*.7+vec2(temporal.x,41.0)),noise(p*.7+vec2(87.0,temporal.y)))-.5;
  p += warp*1.7+temporal;
  float density = 0.0, weight = .55;
  for(int i=0;i<5;i++) {
    density += noise(p)*weight;
    p = p*vec2(2.07,2.03)+vec2(19.0,31.0);
    weight *= .48;
  }
  vec2 radius = (uv-vec2(.5,.44))/vec2(.38,.32);
  float concentration = exp(-2.4*dot(radius,radius));
  float billow = smoothstep(0.0,1.0,(density-.28-(1.0-concentration)*.12)/.45);
  float edge = concentration*sin(3.14159265*uv.x)*sin(3.14159265*uv.y);
  float alpha = (billow*billow*.48+billow*.07)*edge;
  if (ring > .5) {
    // A continuous dense rim holds the circle; smaller radius variations and
    // animated strands keep its edges smoky rather than perfectly geometric.
    float distance = length(center);
    float orbit = .275+energy*.018+(density-.5)*.030;
    float band = exp(-pow((distance-orbit)/(.072+energy*.018),2.0));
    float halo = exp(-pow((distance-.24)/.14,2.0));
    float strands = smoothstep(.22,.75,density);
    alpha = (band*(.50+strands*.40)+halo*strands*.25)*(1.0-smoothstep(.4,.5,distance));
    alpha *= .85+energy*.15;
    // Stable sub-byte dithering breaks up quantized steps in the faint feather.
    float dither = fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453)-.5;
    alpha = clamp(alpha + dither / 255.0 * smoothstep(0.0,.015,alpha),0.0,1.0);
  }
  // Keep the instance hue, with richer smoke rather than pale highlights.
  vec3 tint = ring > .5 ? pow(color,vec3(1.35)) : color;
  gl_FragColor = vec4(tint*alpha,alpha);
}`;

export function InstanceClouds({ color, paused = false, ring = false, energy = 0 }: { color: string; paused?: boolean; ring?: boolean; energy?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const noiseRef = useRef<Uint8Array | null>(null);
  const colorRef = useRef(color);
  const pausedRef = useRef(paused);
  const visualRef = useRef({ ring, energy });
  visualRef.current = { ring, energy };
  const updateRef = useRef<(() => void) | null>(null);
  pausedRef.current = paused;
  useEffect(() => { updateRef.current?.(); }, [paused]);
  const redrawRef = useRef<(() => void) | null>(null);
  colorRef.current = color;
  useEffect(() => { redrawRef.current?.(); }, [color]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const gl = canvas?.getContext('webgl', { alpha: true, antialias: false, depth: false, stencil: false, powerPreference: 'low-power' });
    if (!canvas || !gl) return;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let lastDraw = 0;
    const frameInterval = 1000 / 30;
    let visible = true, aspect = 2, elapsed = 0, previous = performance.now();
    let program: WebGLProgram | null = null;
    let buffer: WebGLBuffer | null = null;
    let texture: WebGLTexture | null = null;
    let uniforms: Record<string, WebGLUniformLocation | null> = {};

    function setup() {
      const shaders: WebGLShader[] = [];
      for (const [type, source] of [
        [gl!.VERTEX_SHADER, 'attribute vec2 position; void main(){ gl_Position=vec4(position,0.0,1.0); }'],
        [gl!.FRAGMENT_SHADER, fragmentSource],
      ] as const) {
        const shader = gl!.createShader(type)!;
        gl!.shaderSource(shader, source);
        gl!.compileShader(shader);
        shaders.push(shader);
      }
      program = gl!.createProgram()!;
      shaders.forEach(shader => gl!.attachShader(program!, shader));
      gl!.linkProgram(program);
      shaders.forEach(shader => gl!.deleteShader(shader));
      if (!gl!.getProgramParameter(program, gl!.LINK_STATUS)) {
        gl!.deleteProgram(program);
        program = null;
        return;
      }
      gl!.useProgram(program);
      buffer = gl!.createBuffer();
      gl!.bindBuffer(gl!.ARRAY_BUFFER, buffer);
      gl!.bufferData(gl!.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl!.STATIC_DRAW);
      const position = gl!.getAttribLocation(program, 'position');
      gl!.enableVertexAttribArray(position);
      gl!.vertexAttribPointer(position, 2, gl!.FLOAT, false, 0, 0);
      texture = gl!.createTexture();
      gl!.bindTexture(gl!.TEXTURE_2D, texture);
      if (!noiseRef.current) {
        noiseRef.current = new Uint8Array(256 * 256);
        for (let i = 0; i < noiseRef.current.length; i++) noiseRef.current[i] = Math.floor(Math.random() * 256);
      }
      gl!.texImage2D(gl!.TEXTURE_2D, 0, gl!.LUMINANCE, 256, 256, 0, gl!.LUMINANCE, gl!.UNSIGNED_BYTE, noiseRef.current);
      gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_MIN_FILTER, gl!.NEAREST);
      gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_MAG_FILTER, gl!.NEAREST);
      gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_WRAP_S, gl!.REPEAT);
      gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_WRAP_T, gl!.REPEAT);
      uniforms = Object.fromEntries(['resolution','aspect','time','color','ring','energy'].map(name => [name, gl!.getUniformLocation(program!, name)]));
    }
    function draw() {
      if (!program || gl!.isContextLost() || document.hidden || !visible) return;
      // Instance appearance colors are validated six-digit hex values.
      const rgb = colorRef.current.match(/[a-f\d]{2}/gi)?.map(value => parseInt(value, 16) / 255) ?? [1,1,1];
      gl!.uniform3f(uniforms.color, rgb[0], rgb[1], rgb[2]);
      gl!.uniform2f(uniforms.resolution, canvas!.width, canvas!.height);
      gl!.uniform1f(uniforms.aspect, aspect);
      // Ring orientation is static; temporal noise still keeps the smoke alive
      // without pulling the whole field around in a circle.
      gl!.uniform1f(uniforms.time, motion.matches ? 0 : elapsed);
      gl!.uniform1f(uniforms.ring, visualRef.current.ring ? 1 : 0);
      gl!.uniform1f(uniforms.energy, motion.matches ? 0 : visualRef.current.energy);
      gl!.drawArrays(gl!.TRIANGLE_STRIP, 0, 4);
    }
    function tick(now: number) {
      elapsed += (now - previous) / 1000;
      previous = now;
      const sinceDraw = now - lastDraw;
      if (sinceDraw >= frameInterval - .5) {
        draw();
        // Keep a steady cadence without accumulating timer drift.
        lastDraw += Math.max(1, Math.floor((sinceDraw + .5) / frameInterval)) * frameInterval;
      }
      frame = requestAnimationFrame(tick);
    }
    function update() {
      cancelAnimationFrame(frame);
      draw();
      if (program && !document.hidden && visible && !pausedRef.current && !motion.matches && !gl!.isContextLost()) {
        previous = lastDraw = performance.now();
        frame = requestAnimationFrame(tick);
      }
    }
    function resize() {
      const bounds = canvas!.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      aspect = bounds.width / bounds.height;
      const ringWidth = Math.max(1, Math.min(880, Math.round(bounds.width * Math.min(devicePixelRatio || 1, 2))));
      canvas!.width = visualRef.current.ring ? ringWidth : 220;
      canvas!.height = visualRef.current.ring ? Math.max(1, Math.round(ringWidth / aspect)) : Math.max(80, Math.min(180, Math.round(220 / aspect)));
      gl!.viewport(0, 0, canvas!.width, canvas!.height);
      draw();
    }
    function lost(event: Event) { event.preventDefault(); cancelAnimationFrame(frame); }
    function restored() { setup(); resize(); update(); }
    setup();
    const observer = new ResizeObserver(resize);
    const intersection = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; update(); });
    observer.observe(canvas);
    intersection.observe(canvas);
    resize();
    update();
    redrawRef.current = draw;
    updateRef.current = update;
    document.addEventListener('visibilitychange', update);
    motion.addEventListener('change', update);
    canvas.addEventListener('webglcontextlost', lost);
    canvas.addEventListener('webglcontextrestored', restored);
    return () => {
      cancelAnimationFrame(frame);
      redrawRef.current = null;
      updateRef.current = null;
      observer.disconnect();
      intersection.disconnect();
      document.removeEventListener('visibilitychange', update);
      motion.removeEventListener('change', update);
      canvas.removeEventListener('webglcontextlost', lost);
      canvas.removeEventListener('webglcontextrestored', restored);
      gl.deleteBuffer(buffer);
      gl.deleteTexture(texture);
      gl.deleteProgram(program);
    };
  }, []);

  return <div className="instance-clouds" aria-hidden="true"><canvas ref={canvasRef} /></div>;
}
