/**
 * Vision2Scale - 3D Reconstruction Calibration
 * Main Application Orchestrator & UI Event Handler
 */

window.Vision2Scale = window.Vision2Scale || {};
window.TrackC = window.Vision2Scale; // Backward compatibility alias

Vision2Scale.App = (function () {

    /**
     * Show Toast Notification
     */
    function showToast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        
        let icon = 'fa-info-circle';
        if (type === 'success') icon = 'fa-check-circle';
        if (type === 'error') icon = 'fa-exclamation-triangle';

        toast.innerHTML = `
            <i class="fas ${icon}" style="color: ${type === 'success' ? '#10b981' : type === 'error' ? '#f43f5e' : '#00f2fe'};"></i>
            <div>
                <div style="font-weight: 600; font-size: 0.85rem;">${type.toUpperCase()}</div>
                <div style="font-size: 0.8rem; color: #94a3b8;">${message}</div>
            </div>
        `;

        container.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100%)';
            toast.style.transition = 'all 0.3s ease-out';
            setTimeout(() => toast.remove(), 300);
        }, 3500);
    }

    /**
     * Setup UI Event Listeners
     */
    function setupEventListeners() {
        // Toolbar controls for 3D viewer
        document.getElementById('tool-reset').addEventListener('click', () => {
            Vision2Scale.Viewer.resetCamera();
            showToast('Camera position reset.', 'info');
        });

        document.getElementById('tool-wireframe').addEventListener('click', (e) => {
            const active = Vision2Scale.Viewer.toggleWireframe();
            e.currentTarget.classList.toggle('active', active);
            showToast(`Wireframe view: ${active ? 'Enabled' : 'Disabled'}`, 'info');
        });

        document.getElementById('tool-grid').addEventListener('click', (e) => {
            const active = Vision2Scale.Viewer.toggleGrid();
            e.currentTarget.classList.toggle('active', active);
            showToast(`Grid floor: ${active ? 'Visible' : 'Hidden'}`, 'info');
        });

        document.getElementById('tool-axes').addEventListener('click', (e) => {
            const active = Vision2Scale.Viewer.toggleAxes();
            e.currentTarget.classList.toggle('active', active);
            showToast(`Coordinate axes: ${active ? 'Visible' : 'Hidden'}`, 'info');
        });

        // IMAGE-TO-3D EVENT HANDLERS
        const imgDropzone = document.getElementById('image-dropzone');
        const imgFileInput = document.getElementById('image-file-input');

        imgDropzone.addEventListener('click', () => imgFileInput.click());

        imgDropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            imgDropzone.classList.add('dragover');
        });

        imgDropzone.addEventListener('dragleave', () => {
            imgDropzone.classList.remove('dragover');
        });

        imgDropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            imgDropzone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                Vision2Scale.ImageTo3D.handleImageFile(e.dataTransfer.files[0]);
            }
        });

        imgFileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                Vision2Scale.ImageTo3D.handleImageFile(e.target.files[0]);
            }
        });

        // Sample Image Preset Buttons
        document.querySelectorAll('.sample-img-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const sample = e.currentTarget.getAttribute('data-sample');
                Vision2Scale.ImageTo3D.loadSampleImage(sample);
            });
        });

        // Generate 3D CTA Button
        document.getElementById('btn-generate-3d').addEventListener('click', () => {
            Vision2Scale.ImageTo3D.reconstruct3DFromCurrentImage();
        });

        // Calibration action buttons
        document.getElementById('btn-calc-scale').addEventListener('click', () => {
            Vision2Scale.Calibration.calculateScale();
        });

        document.getElementById('btn-apply-scale').addEventListener('click', () => {
            Vision2Scale.Calibration.applyScaleToModel();
        });

        document.getElementById('btn-reset-calibration').addEventListener('click', () => {
            Vision2Scale.Calibration.resetCalibration();
        });

        // Drag & Drop OBJ Uploader (Direct OBJ)
        const objDropzone = document.getElementById('obj-dropzone');
        const objFileInput = document.getElementById('obj-file-input');

        objDropzone.addEventListener('click', () => objFileInput.click());

        objDropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            objDropzone.classList.add('dragover');
        });

        objDropzone.addEventListener('dragleave', () => {
            objDropzone.classList.remove('dragover');
        });

        objDropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            objDropzone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                Vision2Scale.OBJLoaderManager.handleFileUpload(e.dataTransfer.files[0]);
            }
        });

        objFileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                Vision2Scale.OBJLoaderManager.handleFileUpload(e.target.files[0]);
            }
        });

        // Simulated API Endpoint Test Buttons
        const testReconstructBtn = document.getElementById('btn-test-post-reconstruct');
        if (testReconstructBtn) {
            testReconstructBtn.addEventListener('click', () => {
                simulateApiCall('POST', '/reconstruct', {
                    image: 'photo.jpg',
                    quality: 'high',
                    format: 'obj'
                }, 'response-post-reconstruct', {
                    job_id: 'rec-' + Math.random().toString(36).substr(2, 6),
                    status: 'completed',
                    input_image: 'uploaded_photo.jpg',
                    output_mesh: 'reconstructed_mesh.obj',
                    vertices: 4096,
                    faces: 8192,
                    confidence_score: 0.985,
                    reconstructed_at: new Date().toISOString()
                });
            });
        }

        document.getElementById('btn-test-post-job').addEventListener('click', () => {
            simulateApiCall('POST', '/jobs', {
                input_mesh: 'calibration_model.obj',
                known_length: parseFloat(document.getElementById('input-known-length').value),
                measured_length: parseFloat(document.getElementById('input-measured-length').value),
                unit: 'meters'
            }, 'response-post-job');
        });

        document.getElementById('btn-test-get-result').addEventListener('click', () => {
            const scale = Vision2Scale.Calibration.getComputedScale();
            simulateApiCall('GET', '/jobs/job-8f92a10c/result', null, 'response-get-result', {
                job_id: 'job-8f92a10c',
                status: 'completed',
                scale_factor: scale,
                input_mesh: 'reconstructed_mesh.obj',
                output_mesh: 'scaled_mesh.obj',
                unit: 'meters',
                processed_at: new Date().toISOString()
            });
        });

        // Smooth Scroll Links
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function (e) {
                e.preventDefault();
                const target = document.querySelector(this.getAttribute('href'));
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth' });
                }
            });
        });
    }

    /**
     * Simulate FastAPI HTTP Endpoint Call
     */
    function simulateApiCall(method, endpoint, body, responseElementId, customResponse = null) {
        showToast(`Sending ${method} request to ${endpoint}...`, 'info');

        const outputEl = document.getElementById(responseElementId);
        if (outputEl) {
            outputEl.textContent = '// Connecting to Vision2Scale FastAPI server...';
        }

        setTimeout(() => {
            let mockData = customResponse;
            if (!mockData) {
                if (method === 'POST') {
                    mockData = {
                        job_id: 'job-' + Math.random().toString(36).substr(2, 9),
                        status: 'processing',
                        message: 'Calibration job queued successfully',
                        created_at: new Date().toISOString()
                    };
                } else {
                    mockData = {
                        job_id: 'job-8f92a10c',
                        status: 'completed',
                        scale_factor: 2.0,
                        input_mesh: 'mesh.obj',
                        output_mesh: 'scaled_mesh.obj',
                        unit: 'meters'
                    };
                }
            }

            if (outputEl) {
                outputEl.textContent = JSON.stringify(mockData, null, 2);
            }
            showToast(`API Response 200 OK from ${endpoint}`, 'success');
        }, 600);
    }

    return {
        init: function () {
            Vision2Scale.Viewer.init();
            setupEventListeners();
            
            // Auto load initial sample image
            setTimeout(() => {
                Vision2Scale.ImageTo3D.loadSampleImage('turbine');
            }, 300);

            showToast('Vision2Scale Dashboard initialized in Demo Mode', 'success');
        },
        showToast: showToast
    };
})();

// Document Ready Initialization
document.addEventListener('DOMContentLoaded', () => {
    Vision2Scale.App.init();
});
