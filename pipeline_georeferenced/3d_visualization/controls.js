import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { FlyControls } from 'three/addons/controls/FlyControls.js';

const _forward = new THREE.Vector3();

/**
 * Sets up both OrbitControls and FlyControls on the same camera/DOM element and
 * exposes a single toggle between them. Default mode: 'orbit'.
 * @param {THREE.Camera} camera
 * @param {HTMLElement} domElement
 * @returns {{setMode:(mode:'orbit'|'fly')=>void, getMode:()=>string, update:(delta:number)=>void}}
 */
export function setupNavigation(camera, domElement) {
  const orbitControls = new OrbitControls(camera, domElement);
  orbitControls.enableDamping = true;
  orbitControls.dampingFactor = 0.08;
  orbitControls.maxPolarAngle = Math.PI / 2 - 0.05; // stop just short of dipping below the ground plane
  orbitControls.minDistance = 5;
  orbitControls.maxDistance = 1000;

  const flyControls = new FlyControls(camera, domElement);
  flyControls.movementSpeed = 60;
  flyControls.rollSpeed = Math.PI / 8;
  flyControls.dragToLook = true;
  flyControls.autoForward = false;
  flyControls.enabled = false;

  // FlyControls attaches its own mouse/key listeners on domElement immediately and
  // keeps them live even while "disabled" - we never call flyControls.update() outside
  // fly mode, so stray key/drag state it records while inactive is simply never
  // applied to the camera.

  let mode = 'orbit';

  function setMode(nextMode) {
    if (nextMode !== 'orbit' && nextMode !== 'fly') {
      throw new Error(`Unknown navigation mode: ${nextMode}`);
    }
    if (nextMode === mode) return;

    if (nextMode === 'fly') {
      orbitControls.enabled = false;
      flyControls.enabled = true;
    } else {
      // FlyControls has no notion of an orbit "target" - re-derive one from wherever
      // the camera is currently pointed, otherwise OrbitControls would yank the view
      // back to its stale pre-fly target on the next interaction.
      camera.getWorldDirection(_forward);
      const distance = camera.position.distanceTo(orbitControls.target) || 50;
      orbitControls.target.copy(camera.position).addScaledVector(_forward, distance);
      flyControls.enabled = false;
      orbitControls.enabled = true;
      orbitControls.update();
    }
    mode = nextMode;
  }

  function getMode() {
    return mode;
  }

  function update(delta) {
    if (mode === 'orbit') {
      orbitControls.update();
    } else {
      flyControls.update(delta);
    }
  }

  return { setMode, getMode, update };
}
