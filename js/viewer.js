/**
 * Vision2Scale - 3D Reconstruction Calibration
 * Three.js 3D Viewer & Hero Canvas Module
 */

window.Vision2Scale = window.Vision2Scale || {};
window.TrackC = window.Vision2Scale;

Vision2Scale.Viewer = (function () {
    let heroScene, heroCamera, heroRenderer, heroMesh;
    let mainScene, mainCamera, mainRenderer, mainControls;
    let currentMesh = null;
    let wireframeMesh = null;
    let bboxHelper = null;
    let gridHelper = null;
    let axesHelper = null;

    // Viewport Options
    let isWireframeActive = false;
    let isGridActive = true;
    let isAxesActive = true;

    // Target scale for lerp animation
    let currentScale = 1.0;
    let targetScale = 1.0;

    /**
     * Initialize Hero Background Canvas Scene
     */
    function initHeroCanvas() {
        const canvas = document.getElementById('hero-canvas');
        if (!canvas) return;

        heroScene = new THREE.Scene();
        heroCamera = new THREE.PerspectiveCamera(45, canvas.clientWidth / canvas.clientHeight, 0.1, 1000);
        heroCamera.position.set(0, 0, 8);

        heroRenderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
        heroRenderer.setSize(canvas.clientWidth, canvas.clientHeight);
        heroRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        // Create futuristic wireframe torus knot
        const geometry = new THREE.TorusKnotGeometry(2, 0.6, 120, 16);
        const material = new THREE.MeshBasicMaterial({
            color: 0x00f2fe,
            wireframe: true,
            transparent: true,
            opacity: 0.25
        });

        heroMesh = new THREE.Mesh(geometry, material);
        heroScene.add(heroMesh);

        // Ambient particles
        const particleGeo = new THREE.BufferGeometry();
        const count = 200;
        const posArray = new Float32Array(count * 3);
        for (let i = 0; i < count * 3; i++) {
            posArray[i] = (Math.random() - 0.5) * 20;
        }
        particleGeo.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
        const particleMat = new THREE.PointsMaterial({
            size: 0.05,
            color: 0x4facfe,
            transparent: true,
            opacity: 0.5
        });
        const particles = new THREE.Points(particleGeo, particleMat);
        heroScene.add(particles);

        function animateHero() {
            requestAnimationFrame(animateHero);
            if (heroMesh) {
                heroMesh.rotation.x += 0.003;
                heroMesh.rotation.y += 0.005;
            }
            particles.rotation.y += 0.001;
            heroRenderer.render(heroScene, heroCamera);
        }
        animateHero();
    }

    /**
     * Initialize Main 3D Interactive Viewport
     */
    function initMainViewer() {
        const container = document.getElementById('viewer-card-container');
        const canvas = document.getElementById('main-3d-canvas');
        if (!canvas || !container) return;

        mainScene = new THREE.Scene();
        mainScene.background = new THREE.Color(0x04060c);

        mainCamera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
        mainCamera.position.set(6, 6, 8);

        mainRenderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
        mainRenderer.setSize(container.clientWidth, container.clientHeight);
        mainRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        mainRenderer.shadowMap.enabled = true;

        // Orbit Controls
        if (THREE.OrbitControls) {
            mainControls = new THREE.OrbitControls(mainCamera, mainRenderer.domElement);
            mainControls.enableDamping = true;
            mainControls.dampingFactor = 0.05;
            mainControls.maxPolarAngle = Math.PI / 2 + 0.1;
        }

        // Lighting Setup
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
        mainScene.add(ambientLight);

        const dirLight1 = new THREE.DirectionalLight(0x00f2fe, 1.2);
        dirLight1.position.set(10, 15, 10);
        dirLight1.castShadow = true;
        mainScene.add(dirLight1);

        const dirLight2 = new THREE.DirectionalLight(0x8b5cf6, 0.8);
        dirLight2.position.set(-10, -10, -10);
        mainScene.add(dirLight2);

        // Grid Helper
        gridHelper = new THREE.GridHelper(20, 20, 0x00f2fe, 0x1e293b);
        gridHelper.position.y = -0.01;
        mainScene.add(gridHelper);

        // Axes Helper
        axesHelper = new THREE.AxesHelper(3);
        mainScene.add(axesHelper);

        // Create default calibration cube mesh
        createCubeMesh();

        // Render Loop
        function animateMain() {
            requestAnimationFrame(animateMain);

            if (mainControls) mainControls.update();

            // Smooth scale lerping animation
            if (currentMesh && Math.abs(currentScale - targetScale) > 0.001) {
                currentScale += (targetScale - currentScale) * 0.08;
                currentMesh.scale.set(currentScale, currentScale, currentScale);
                if (wireframeMesh) wireframeMesh.scale.set(currentScale, currentScale, currentScale);
                if (bboxHelper) bboxHelper.update();
                updateDimensionUI();
            }

            mainRenderer.render(mainScene, mainCamera);
        }
        animateMain();

        // Window Resize Handler
        window.addEventListener('resize', onWindowResize);
    }

    /**
     * Create Initial 3D Mesh (Calibration Cube)
     */
    function createCubeMesh() {
        const geometry = new THREE.BoxGeometry(2, 2, 2);
        
        // Cyberpunk metallic shader material
        const material = new THREE.MeshStandardMaterial({
            color: 0x0d1e3a,
            metalness: 0.6,
            roughness: 0.2,
            wireframe: false,
            emissive: 0x002c48,
            emissiveIntensity: 0.4
        });

        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.y = 1;
        mesh.castShadow = true;
        mesh.receiveShadow = true;

        // Wireframe overlay edge lines
        const edges = new THREE.EdgesGeometry(geometry);
        const lineMat = new THREE.LineBasicMaterial({ color: 0x00f2fe, linewidth: 2 });
        const wireframe = new THREE.LineSegments(edges, lineMat);
        mesh.add(wireframe);

        setMesh(mesh, { vertices: 8, faces: 12, name: 'Standard Calibration Cube' });
    }

    /**
     * Set active mesh in scene and update metadata
     */
    function setMesh(mesh, info = {}) {
        if (currentMesh) mainScene.remove(currentMesh);
        if (bboxHelper) mainScene.remove(bboxHelper);

        currentMesh = mesh;
        mainScene.add(currentMesh);

        // Create Bounding Box Helper
        bboxHelper = new THREE.BoxHelper(currentMesh, 0x10b981);
        mainScene.add(bboxHelper);

        // Reset scale
        currentScale = 1.0;
        targetScale = 1.0;
        currentMesh.scale.set(1, 1, 1);

        // Update info panel
        updateInfoPanel(info);
        updateDimensionUI();
    }

    /**
     * Update Model Information Overlay
     */
    function updateInfoPanel(info) {
        if (info.vertices !== undefined) document.getElementById('info-vertices').textContent = info.vertices.toLocaleString();
        if (info.faces !== undefined) document.getElementById('info-faces').textContent = info.faces.toLocaleString();
        if (info.name !== undefined) document.getElementById('info-format').textContent = info.name;
    }

    /**
     * Update Dynamic Bounding Dimensions Overlay
     */
    function updateDimensionUI() {
        if (!currentMesh) return;

        const box = new THREE.Box3().setFromObject(currentMesh);
        const size = new THREE.Vector3();
        box.getSize(size);

        // Format to 2 decimal places
        const lengthM = (size.x).toFixed(2);
        const widthM = (size.z).toFixed(2);
        const heightM = (size.y).toFixed(2);

        document.getElementById('dim-length').textContent = `${lengthM} m`;
        document.getElementById('dim-width').textContent = `${widthM} m`;
        document.getElementById('dim-height').textContent = `${heightM} m`;
    }

    /**
     * Set target scale factor for animation
     */
    function setScaleFactor(scale) {
        targetScale = parseFloat(scale);
    }

    /**
     * Camera View Controllers
     */
    function resetCamera() {
        if (mainCamera && mainControls) {
            mainCamera.position.set(6, 6, 8);
            mainControls.target.set(0, 1, 0);
            mainControls.update();
        }
    }

    function toggleWireframe() {
        isWireframeActive = !isWireframeActive;
        if (currentMesh) {
            currentMesh.material.wireframe = isWireframeActive;
        }
        return isWireframeActive;
    }

    function toggleGrid() {
        isGridActive = !isGridActive;
        if (gridHelper) gridHelper.visible = isGridActive;
        return isGridActive;
    }

    function toggleAxes() {
        isAxesActive = !isAxesActive;
        if (axesHelper) axesHelper.visible = isAxesActive;
        return isAxesActive;
    }

    function onWindowResize() {
        const heroCanvas = document.getElementById('hero-canvas');
        if (heroCanvas && heroRenderer && heroCamera) {
            heroCamera.aspect = heroCanvas.clientWidth / heroCanvas.clientHeight;
            heroCamera.updateProjectionMatrix();
            heroRenderer.setSize(heroCanvas.clientWidth, heroCanvas.clientHeight);
        }

        const mainContainer = document.getElementById('viewer-card-container');
        if (mainContainer && mainRenderer && mainCamera) {
            mainCamera.aspect = mainContainer.clientWidth / mainContainer.clientHeight;
            mainCamera.updateProjectionMatrix();
            mainRenderer.setSize(mainContainer.clientWidth, mainContainer.clientHeight);
        }
    }

    return {
        init: function () {
            initHeroCanvas();
            initMainViewer();
        },
        setMesh: setMesh,
        setScaleFactor: setScaleFactor,
        resetCamera: resetCamera,
        toggleWireframe: toggleWireframe,
        toggleGrid: toggleGrid,
        toggleAxes: toggleAxes,
        getTargetScale: function () { return targetScale; },
        getCurrentScale: function () { return currentScale; }
    };
})();
