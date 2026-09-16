/**
 * Vision2Scale - 3D Reconstruction Calibration
 * Image-to-3D Reconstruction Engine Module
 */

window.Vision2Scale = window.Vision2Scale || {};
window.TrackC = window.Vision2Scale;

Vision2Scale.ImageTo3D = (function () {
    let currentImageElement = null;
    let currentImageUrl = null;

    /**
     * Preset Sample Images with corresponding 3D geometries
     */
    const SAMPLE_IMAGES = {
        turbine: {
            name: 'Industrial Turbine Specimen',
            url: 'https://images.unsplash.com/photo-1581092160607-ee22621dd758?w=500&auto=format&fit=crop&q=80',
            type: 'gear',
            vertices: 1600,
            faces: 3200
        },
        component: {
            name: 'Calibration Cube Block',
            url: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=500&auto=format&fit=crop&q=80',
            type: 'cube',
            vertices: 8,
            faces: 12
        },
        artifact: {
            name: 'Archaeological Vessel Specimen',
            url: 'https://images.unsplash.com/photo-1579783902614-a3fb3927b675?w=500&auto=format&fit=crop&q=80',
            type: 'cylinder',
            vertices: 1024,
            faces: 2048
        }
    };

    /**
     * Convert 2D Image into a 3D Heightmap/Displacement Surface Mesh using HTML5 Canvas
     */
    function convertImageTo3DMesh(imgElement, callback) {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');

        // Target grid resolution for 3D reconstruction
        const width = 64;
        const height = 64;
        canvas.width = width;
        canvas.height = height;

        ctx.drawImage(imgElement, 0, 0, width, height);
        const imgData = ctx.getImageData(0, 0, width, height).data;

        // Create 3D Plane Geometry
        const geometry = new THREE.PlaneGeometry(3, 3, width - 1, height - 1);
        const posAttribute = geometry.attributes.position;

        // Displace Z coordinates based on image brightness (Luminance depth field)
        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                const idx = (y * width + x) * 4;
                const r = imgData[idx];
                const g = imgData[idx + 1];
                const b = imgData[idx + 2];
                // Luminance calculation
                const brightness = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0;
                
                const vertIdx = y * width + x;
                // Displace Z-axis for 3D depth effect
                posAttribute.setZ(vertIdx, brightness * 0.8);
            }
        }

        geometry.computeVertexNormals();

        // Create Three.js Texture from uploaded image
        const texture = new THREE.CanvasTexture(imgElement);
        texture.needsUpdate = true;

        const material = new THREE.MeshStandardMaterial({
            map: texture,
            metalness: 0.3,
            roughness: 0.4,
            side: THREE.DoubleSide
        });

        const mesh = new THREE.Mesh(geometry, material);
        mesh.rotation.x = -Math.PI / 3;
        mesh.position.y = 1;

        // Wireframe line overlay
        const wireframeMat = new THREE.MeshBasicMaterial({
            color: 0x00f2fe,
            wireframe: true,
            transparent: true,
            opacity: 0.15
        });
        const wireframeMesh = new THREE.Mesh(geometry, wireframeMat);
        mesh.add(wireframeMesh);

        const vertexCount = posAttribute.count;
        const faceCount = geometry.index ? geometry.index.count / 3 : (width - 1) * (height - 1) * 2;

        if (callback) {
            callback(mesh, {
                vertices: vertexCount,
                faces: faceCount,
                name: 'Reconstructed 3D Mesh from Photo'
            });
        }
    }

    /**
     * Handle Image File Upload (User Photo)
     */
    function handleImageFile(file) {
        if (!file || !file.type.startsWith('image/')) {
            Vision2Scale.App.showToast('Please select a valid image file (JPG, PNG, WEBP).', 'error');
            return;
        }

        Vision2Scale.App.showToast(`Loading image ${file.name}...`, 'info');

        const reader = new FileReader();
        reader.onload = function (e) {
            currentImageUrl = e.target.result;
            const img = new Image();
            img.crossOrigin = 'Anonymous';
            img.onload = function () {
                currentImageElement = img;
                render2DPreview(img);
                Vision2Scale.App.showToast('Image loaded! Click "Generate 3D Model" to reconstruct.', 'success');
            };
            img.src = currentImageUrl;
        };
        reader.readAsDataURL(file);
    }

    /**
     * Load Sample Preset Image
     */
    function loadSampleImage(key) {
        const sample = SAMPLE_IMAGES[key];
        if (!sample) return;

        Vision2Scale.App.showToast(`Loading sample image: ${sample.name}...`, 'info');

        const img = new Image();
        img.crossOrigin = 'Anonymous';
        img.onload = function () {
            currentImageElement = img;
            currentImageUrl = sample.url;
            render2DPreview(img);
            
            // Auto generate 3D model for sample presets
            reconstruct3DFromCurrentImage();
        };
        img.onerror = function () {
            // Fallback generated canvas pattern if offline
            createFallbackSampleCanvas(sample.name, (fallbackImg) => {
                currentImageElement = fallbackImg;
                render2DPreview(fallbackImg);
                reconstruct3DFromCurrentImage();
            });
        };
        img.src = sample.url;
    }

    /**
     * Create offline synthetic procedural canvas pattern if web image is unreachable
     */
    function createFallbackSampleCanvas(title, callback) {
        const canvas = document.createElement('canvas');
        canvas.width = 400;
        canvas.height = 400;
        const ctx = canvas.getContext('2d');

        // Draw radial cyberpunk pattern
        const grad = ctx.createRadialGradient(200, 200, 20, 200, 200, 200);
        grad.addColorStop(0, '#00f2fe');
        grad.addColorStop(0.5, '#4facfe');
        grad.addColorStop(1, '#070a12');
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, 400, 400);

        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 4;
        ctx.strokeRect(50, 50, 300, 300);

        ctx.fillStyle = '#ffffff';
        ctx.font = '20px Orbitron, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(title, 200, 200);

        const img = new Image();
        img.onload = () => callback(img);
        img.src = canvas.toDataURL();
    }

    /**
     * Render 2D Image Preview with detected feature points overlay
     */
    function render2DPreview(img) {
        const previewContainer = document.getElementById('image-2d-preview-box');
        if (!previewContainer) return;

        previewContainer.innerHTML = '';
        
        const wrapper = document.createElement('div');
        wrapper.style.position = 'relative';
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.display = 'flex';
        wrapper.style.alignItems = 'center';
        wrapper.style.justifyContent = 'center';
        wrapper.style.overflow = 'hidden';

        const imgEl = document.createElement('img');
        imgEl.src = img.src;
        imgEl.style.maxWidth = '100%';
        imgEl.style.maxHeight = '320px';
        imgEl.style.borderRadius = '8px';
        imgEl.style.objectFit = 'contain';

        // Feature keypoint overlay canvas
        const overlayCanvas = document.createElement('canvas');
        overlayCanvas.style.position = 'absolute';
        overlayCanvas.style.top = '0';
        overlayCanvas.style.left = '0';
        overlayCanvas.style.width = '100%';
        overlayCanvas.style.height = '100%';
        overlayCanvas.style.pointerEvents = 'none';

        wrapper.appendChild(imgEl);
        wrapper.appendChild(overlayCanvas);
        previewContainer.appendChild(wrapper);

        // Draw simulated computer vision keypoints (SfM feature matches)
        setTimeout(() => {
            overlayCanvas.width = wrapper.clientWidth;
            overlayCanvas.height = wrapper.clientHeight;
            const ctx = overlayCanvas.getContext('2d');
            
            ctx.fillStyle = '#00f2fe';
            ctx.strokeStyle = 'rgba(0, 242, 254, 0.4)';
            ctx.lineWidth = 1;

            // Draw 25 simulated feature points
            for (let i = 0; i < 25; i++) {
                const x = 50 + Math.random() * (overlayCanvas.width - 100);
                const y = 50 + Math.random() * (overlayCanvas.height - 100);

                ctx.beginPath();
                ctx.arc(x, y, 3, 0, Math.PI * 2);
                ctx.fill();

                ctx.beginPath();
                ctx.arc(x, y, 8, 0, Math.PI * 2);
                ctx.stroke();
            }
        }, 100);
    }

    /**
     * Execute Image to 3D Reconstruction Process
     */
    function reconstruct3DFromCurrentImage() {
        if (!currentImageElement) {
            Vision2Scale.App.showToast('Please upload or select an image first.', 'error');
            return;
        }

        Vision2Scale.App.showToast('Reconstructing 3D Mesh geometry from 2D Photo...', 'info');

        // Show reconstruction loading animation
        const statusEl = document.getElementById('reconstruct-status-text');
        if (statusEl) statusEl.textContent = 'Extracting feature points & depth map...';

        setTimeout(() => {
            if (statusEl) statusEl.textContent = 'Generating 3D surface mesh & UV texture...';

            setTimeout(() => {
                convertImageTo3DMesh(currentImageElement, (mesh, info) => {
                    Vision2Scale.Viewer.setMesh(mesh, info);
                    if (statusEl) statusEl.textContent = '3D Reconstruction Complete!';
                    Vision2Scale.App.showToast('3D Reconstruction successfully completed!', 'success');
                    
                    // Trigger calibration pipeline animation
                    Vision2Scale.Pipeline.triggerAnimation();
                });
            }, 600);
        }, 600);
    }

    return {
        handleImageFile: handleImageFile,
        loadSampleImage: loadSampleImage,
        reconstruct3DFromCurrentImage: reconstruct3DFromCurrentImage
    };
})();
