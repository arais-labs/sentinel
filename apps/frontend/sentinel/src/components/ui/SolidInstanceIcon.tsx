import { type ReactNode, useEffect, useRef } from 'react';

const vertexSource = `
attribute vec3 position;
attribute vec3 normal;
uniform float angle;
varying float light;
void main() {
  float c=cos(angle), s=sin(angle);
  mat3 turn=mat3(c,0.,-s, 0.,1.,0., s,0.,c);
  float tilt=-.20944;
  mat3 pitch=mat3(1.,0.,0., 0.,cos(tilt),sin(tilt), 0.,-sin(tilt),cos(tilt));
  vec3 p=pitch*turn*position;
  vec3 n=normalize(pitch*turn*normal);
  light=.55+.45*max(0.,dot(n,normalize(vec3(-.4,.6,1.))));
  gl_Position=vec4(p.x*.8,p.y*.7111,-p.z*.1,1.-p.z*.12);
}`;
const fragmentSource = `
precision mediump float;
uniform vec3 color;
varying float light;
void main(){ gl_FragColor=vec4(color*light,1.); }
`;

// Build the bevel once from the silhouette. Flat interiors stay merged into
// runs; only the rounded edges need additional geometry during rotation.
function extrude(pixels: Uint8ClampedArray, size: number, radius: number) {
  const vertices: number[] = [];
  const filled = (x: number, y: number) => x >= 0 && y >= 0 && x < size && y < size && pixels[(y * size + x) * 4 + 3] >= 128;
  const coord = (x: number, y: number, z: number) => [x / size * 2 - 1, 1 - y / size * 2, z];
  function quad(a: number[], b: number[], c: number[], d: number[], n: number[]) {
    for (const point of [a,b,c,a,c,d]) vertices.push(...point,...n);
  }
  const depth = .15, width = size + 1;
  const distance = new Float32Array(width * width);
  // Distance from mesh vertices to the silhouette, in raster pixels.
  for (let y=0;y<=size;y++) for (let x=0;x<=size;x++) {
    distance[y*width+x] = filled(x-1,y-1) && filled(x,y-1) && filled(x-1,y) && filled(x,y) ? size : 0;
  }
  const diagonal = Math.SQRT2;
  for (let y=1;y<size;y++) for (let x=1;x<size;x++) {
    const i=y*width+x;
    distance[i]=Math.min(distance[i],distance[i-1]+1,distance[i-width]+1,distance[i-width-1]+diagonal,distance[i-width+1]+diagonal);
  }
  for (let y=size-1;y>0;y--) for (let x=size-1;x>0;x--) {
    const i=y*width+x;
    distance[i]=Math.min(distance[i],distance[i+1]+1,distance[i+width]+1,distance[i+width+1]+diagonal,distance[i+width-1]+diagonal);
  }
  const at = (x: number, y: number) => distance[Math.max(0,Math.min(size,y))*width+Math.max(0,Math.min(size,x))];
  const heights = new Float32Array(width * width);
  const normals = new Float32Array(width * width * 3);
  for (let y=0;y<=size;y++) for (let x=0;x<=size;x++) {
    const i=y*width+x, d=Math.min(radius,distance[i]*2/size);
    const side=(radius-d)/radius, face=Math.sqrt(Math.max(0,1-side*side));
    const dx=at(x+1,y)-at(x-1,y), dy=at(x,y+1)-at(x,y-1);
    const length=Math.hypot(dx,dy);
    heights[i]=depth-radius+radius*face;
    normals[i*3]=length ? -dx/length*side : 0;
    normals[i*3+1]=length ? dy/length*side : 0;
    normals[i*3+2]=length ? face : 1;
  }
  function face(x: number, y: number, end: number, sign: number) {
    for (const [vx,vy] of [[x,y],[end,y],[end,y+1],[x,y],[end,y+1],[x,y+1]]) {
      const i=vy*width+vx;
      vertices.push(...coord(vx,vy,heights[i]*sign),normals[i*3],normals[i*3+1],normals[i*3+2]*sign);
    }
  }
  const flat = (x: number, y: number) => Math.min(at(x,y),at(x+1,y),at(x,y+1),at(x+1,y+1))*2/size >= radius;
  for (let y=0;y<size;y++) {
    for (let x=0;x<size;x++) {
      if (!filled(x,y)) continue;
      const start=x;
      if (flat(x,y)) while (x+1<size && filled(x+1,y) && flat(x+1,y)) x++;
      for (const sign of [-1,1]) face(start,y,x+1,sign);
    }
    for (let x=0;x<size;x++) {
      if (!filled(x,y)) continue;
      for (const [dx,dy,ax,ay,bx,by] of [[-1,0,x,y,x,y+1],[1,0,x+1,y,x+1,y+1],[0,-1,x,y,x+1,y],[0,1,x,y+1,x+1,y+1]]) {
        if (!filled(x+dx,y+dy)) quad(coord(ax,ay,-depth+radius),coord(bx,by,-depth+radius),coord(bx,by,depth-radius),coord(ax,ay,depth-radius),[dx,-dy,0]);
      }
    }
  }
  return new Float32Array(vertices);
}

export function SolidInstanceIcon({ color, children, paused = false, bevel = .025 }: { color: string; children: ReactNode; paused?: boolean; bevel?: number }) {
  const sourceRef = useRef<HTMLSpanElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const colorRef = useRef(color);
  const pausedRef = useRef(paused);
  const updateRef = useRef<(() => void) | null>(null);
  pausedRef.current = paused;
  useEffect(() => { updateRef.current?.(); }, [paused]);
  const redrawRef = useRef<(() => void) | null>(null);
  colorRef.current=color;
  useEffect(() => { redrawRef.current?.(); },[color]);
  useEffect(() => {
    const canvas=canvasRef.current, source=sourceRef.current;
    const gl=canvas?.getContext('webgl',{alpha:true,antialias:true,depth:true,powerPreference:'low-power'});
    if (!canvas || !source || !gl) return;
    const program=gl.createProgram()!;
    for (const [type,code] of [[gl.VERTEX_SHADER,vertexSource],[gl.FRAGMENT_SHADER,fragmentSource]] as const) {
      const shader=gl.createShader(type)!;
      gl.shaderSource(shader,code); gl.compileShader(shader); gl.attachShader(program,shader); gl.deleteShader(shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program,gl.LINK_STATUS)) { gl.deleteProgram(program); return; }
    gl.useProgram(program);
    const buffer=gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
    for (const [name,offset] of [['position',0],['normal',12]] as const) {
      const location=gl.getAttribLocation(program,name);
      gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location,3,gl.FLOAT,false,24,offset);
    }
    gl.enable(gl.DEPTH_TEST);
    const angleLocation=gl.getUniformLocation(program,'angle');
    const colorLocation=gl.getUniformLocation(program,'color');
    const motion=matchMedia('(prefers-reduced-motion: reduce)');
    let count=0, angle=0, previous=performance.now(), visible=true, disposed=false, generation=0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let imageUrl: string | undefined;
    const scale=Math.min(devicePixelRatio || 1,2);
    canvas.width=Math.round(272*scale); canvas.height=Math.round(306*scale);
    gl.viewport(0,0,canvas.width,canvas.height);
    function draw() {
      if (disposed || document.hidden || !visible || gl!.isContextLost()) return;
      const rgb=colorRef.current.match(/[a-f\d]{2}/gi)?.map(v=>parseInt(v,16)/255) ?? [1,1,1];
      gl!.uniform3f(colorLocation,rgb[0],rgb[1],rgb[2]);
      gl!.uniform1f(angleLocation,motion.matches ? -.4363 : angle);
      gl!.clear(gl!.COLOR_BUFFER_BIT | gl!.DEPTH_BUFFER_BIT);
      gl!.drawArrays(gl!.TRIANGLES,0,count);
    }
    function tick() {
      const now=performance.now(); angle+=(now-previous)/14000*Math.PI*2; previous=now;
      draw(); timer=setTimeout(tick,1000/30);
    }
    function update() {
      clearTimeout(timer); draw();
      if (!document.hidden && visible && !pausedRef.current && !motion.matches && count && !gl!.isContextLost()) {
        previous=performance.now(); timer=setTimeout(tick,1000/30);
      }
    }
    function rebuild() {
      const current=++generation;
      // Match the enlarged icon on Retina displays without rebuilding per frame.
      const detail = 512;
      const raster=document.createElement('canvas'); raster.width=raster.height=detail;
      const ctx=raster.getContext('2d')!;
      const upload=()=> {
        if (disposed || current!==generation) return;
        const mesh=extrude(ctx.getImageData(0,0,detail,detail).data,detail,Math.max(.001,Math.min(.14,bevel)));
        gl!.bindBuffer(gl!.ARRAY_BUFFER,buffer); gl!.bufferData(gl!.ARRAY_BUFFER,mesh,gl!.STATIC_DRAW);
        count=mesh.length/6; canvas!.style.opacity=count ? '1':'0'; source!.style.opacity=count ? '0':'1'; update();
      };
      const svg=source!.querySelector('svg');
      if (imageUrl) { URL.revokeObjectURL(imageUrl); imageUrl=undefined; }
      if (svg) {
        const clone=svg.cloneNode(true) as SVGElement;
        clone.setAttribute('xmlns','http://www.w3.org/2000/svg'); clone.setAttribute('width',String(detail)); clone.setAttribute('height',String(detail)); clone.setAttribute('color','white');
        const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)],{type:'image/svg+xml'}));
        imageUrl=url;
        const image=new Image();
        image.onload=()=> { if (!disposed && current===generation) { ctx.drawImage(image,0,0,detail,detail); upload(); } URL.revokeObjectURL(url); };
        image.onerror=()=>URL.revokeObjectURL(url); image.src=url;
      } else if (source!.textContent) {
        ctx.fillStyle='white'; ctx.font=`450 ${detail * .9}px system-ui`; ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(source!.textContent,detail/2,detail/2,detail); upload();
      }
    }
    const observer=new MutationObserver(rebuild); observer.observe(source,{childList:true,subtree:true,characterData:true});
    const intersection=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;update();}); intersection.observe(canvas);
    const lost=(event: Event)=>{event.preventDefault();clearTimeout(timer);canvas.style.opacity='0';source.style.opacity='1';};
    // Leave the static SVG visible after context loss rather than restarting a
    // renderer under resource pressure.
    canvas.addEventListener('webglcontextlost',lost);
    document.addEventListener('visibilitychange',update); motion.addEventListener('change',update);
    redrawRef.current=draw; updateRef.current=update; rebuild();
    return ()=> {
      disposed=true; generation++; clearTimeout(timer); observer.disconnect(); intersection.disconnect(); redrawRef.current=null; updateRef.current=null;
      if(imageUrl) URL.revokeObjectURL(imageUrl);
      document.removeEventListener('visibilitychange',update); motion.removeEventListener('change',update); canvas.removeEventListener('webglcontextlost',lost);
      gl.deleteBuffer(buffer); gl.deleteProgram(program);
    };
  },[bevel]);
  return <div className="instance-solid-icon"><span ref={sourceRef}>{children}</span><canvas ref={canvasRef} /></div>;
}
