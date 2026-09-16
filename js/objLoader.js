/**
 * Vision2Scale - 3D Reconstruction Calibration
 * OBJ File Loader & Preset Mesh Manager
 */

window.Vision2Scale = window.Vision2Scale || {};
window.TrackC = window.Vision2Scale;

Vision2Scale.OBJLoaderManager = (function () {

    /**
     * Load built-in preset meshes
     */
    function loadPreset(presetType) {
        let geometry, material, name, vertices, faces;

        if (presetType === 'cube') {
            geometry = new THREE.BoxGeometry(2, 2, 2);
            name = 'Calibration Cube';
            vertices = 8;
            faces = 12;
        } else if (presetType === 'gear') {
            geometry = new THREE.TorusKnotGeometry(1.2, 0.4, 100, 16);
            name = 'Industrial Component';
            vertices = geometry.attributes.position.count;
            faces = geometry.index ? geometry.index.count / 3 : vertices / 3;
        } else if (presetType === 'cylinder') {
            geometry = new THREE.CylinderGeometry(1, 1, 2.5, 32);
            name = 'Standard Specimen Cylinder';
            vertices = geometry.attributes.position.count;
            faces = geometry.index ? geometry.index.count / 3 : vertices / 3;
        }

        const mat = new THREE.MeshStandardMaterial({
            color: 0x0d1e3a,
            metalness: 0.6,
            roughness: 0.25,
            emissive: 0x002c48,
            emissiveIntensity: 0.3
        });

        const mesh = new THREE.Mesh(geometry, mat);
        mesh.position.y = 1;

        // Wireframe edges
        const edges = new THREE.EdgesGeometry(geometry);
        const lineMat = new THREE.LineBasicMaterial({ color: 0x00f2fe, linewidth: 2 });
        const wireframe = new THREE.LineSegments(edges, lineMat);
        mesh.add(wireframe);

        Vision2Scale.Viewer.setMesh(mesh, {
            vertices: Math.round(vertices),
            faces: Math.round(faces),
            name: name
        });

        Vision2Scale.App.showToast(`Loaded preset: ${name}`, 'info');
    }

    /**
     * Parse raw OBJ text string into Three.js Geometry
     */
    function parseOBJText(text) {
        const lines = text.split('\n');
        const positions = [];
        const indices = [];

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            if (line.startsWith('v ')) {
                const parts = line.split(/\s+/).slice(1).map(Number);
                positions.push(parts[0], parts[1], parts[2]);
            } else if (line.startsWith('f ')) {
                const parts = line.split(/\s+/).slice(1);
                // Handle face formats like f v1/vt1/vn1 v2/vt2/vn2 v3/vt3/vn3
                const vIndices = parts.map(p => parseInt(p.split('/')[0]) - 1);
                if (vIndices.length >= 3) {
                    indices.push(vIndices[0], vIndices[1], vIndices[2]);
                    if (vIndices.length === 4) { // Quad conversion to triangles
                        indices.push(vIndices[0], vIndices[2], vIndices[3]);
                    }
                }
            }
        }

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
        if (indices.length > 0) {
            geometry.setIndex(indices);
        }
        geometry.computeVertexNormals();

        return {
            geometry: geometry,
            vertexCount: positions.length / 3,
            faceCount: indices.length > 0 ? indices.length / 3 : (positions.length / 9)
        };
    }

    /**
     * Read and load user-selected `.obj` file
     */
    function handleFileUpload(file) {
        if (!file || !file.name.toLowerCase().endsWith('.obj')) {
            Vision2Scale.App.showToast('Please select a valid .OBJ file format.', 'error');
            return;
        }

        Vision2Scale.App.showToast(`Parsing ${file.name}...`, 'info');

        const reader = new FileReader();
        reader.onload = function (e) {
            try {
                const content = e.target.result;
                const parsed = parseOBJText(content);

                const material = new THREE.MeshStandardMaterial({
                    color: 0x1e293b,
                    metalness: 0.5,
                    roughness: 0.3,
                    emissive: 0x00f2fe,
                    emissiveIntensity: 0.1
                });

                const mesh = new THREE.Mesh(parsed.geometry, material);
                
                // Auto-center geometry
                parsed.geometry.center();
                
                // Position above grid
                parsed.geometry.computeBoundingBox();
                const box = parsed.geometry.boundingBox;
                const height = box.max.y - box.min.y;
                mesh.position.y = height / 2;

                // Wireframe edges
                const edges = new THREE.EdgesGeometry(parsed.geometry);
                const lineMat = new THREE.LineBasicMaterial({ color: 0x00f2fe, linewidth: 1.5 });
                const wireframe = new THREE.LineSegments(edges, lineMat);
                mesh.add(wireframe);

                Vision2Scale.Viewer.setMesh(mesh, {
                    vertices: Math.round(parsed.vertexCount),
                    faces: Math.round(parsed.faceCount),
                    name: file.name
                });

                document.getElementById('upload-file-info').textContent = `Loaded: ${file.name} (${Math.round(parsed.vertexCount)} vertices)`;
                Vision2Scale.App.showToast(`Successfully loaded model: ${file.name}`, 'success');

            } catch (err) {
                console.error(err);
                Vision2Scale.App.showToast('Failed to parse .OBJ file structure.', 'error');
            }
        };

        reader.readAsText(file);
    }

    return {
        loadPreset: loadPreset,
        handleFileUpload: handleFileUpload
    };
})();
