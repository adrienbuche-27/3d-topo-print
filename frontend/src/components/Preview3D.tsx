import { Bounds, Center, OrbitControls, useGLTF } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { Suspense } from 'react'

interface Props {
  url: string
}

function Model({ url }: Props) {
  const { scene } = useGLTF(url)
  // The model is in print millimetres with z up; three.js scenes are y up.
  return (
    <Center rotation={[-Math.PI / 2, 0, 0]}>
      <primitive object={scene} />
    </Center>
  )
}

export function Preview3D({ url }: Props) {
  return (
    <div className="preview">
      <Canvas camera={{ position: [0, 180, 260], fov: 35, near: 1, far: 5000 }} shadows={false}>
        <color attach="background" args={['#eef1f4']} />
        <hemisphereLight args={['#ffffff', '#8a8f99', 1.2]} />
        <directionalLight position={[-150, 300, 120]} intensity={2.2} />
        <directionalLight position={[200, 100, -150]} intensity={0.5} />
        <Suspense fallback={null}>
          <Bounds fit clip observe margin={1.15} key={url}>
            <Model url={url} />
          </Bounds>
        </Suspense>
        <OrbitControls makeDefault enableDamping />
      </Canvas>
      <span className="preview__hint">Drag to rotate · scroll to zoom · right-drag to pan</span>
    </div>
  )
}
