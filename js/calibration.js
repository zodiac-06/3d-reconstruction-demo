/**
 * Vision2Scale - 3D Reconstruction Calibration
 * Calibration Logic & Scale Factor Engine
 */

window.Vision2Scale = window.Vision2Scale || {};
window.TrackC = window.Vision2Scale;

Vision2Scale.Calibration = (function () {
    let knownLength = 10.0;
    let measuredLength = 5.0;
    let computedScaleFactor = 2.0;

    /**
     * Calculate Scale Factor from inputs
     * Scale Factor = Known Real-World Length / Measured Model Length
     */
    function calculateScale() {
        const knownInput = parseFloat(document.getElementById('input-known-length').value);
        const measuredInput = parseFloat(document.getElementById('input-measured-length').value);

        if (isNaN(knownInput) || isNaN(measuredInput) || measuredInput <= 0 || knownInput <= 0) {
            Vision2Scale.App.showToast('Please enter valid positive numbers for lengths.', 'error');
            return null;
        }

        knownLength = knownInput;
        measuredLength = measuredInput;

        computedScaleFactor = knownLength / measuredLength;

        // Update UI displays
        document.getElementById('display-scale-factor').textContent = `${computedScaleFactor.toFixed(2)}×`;
        
        Vision2Scale.App.showToast(`Scale Factor calculated: ${computedScaleFactor.toFixed(2)}×`, 'success');
        return computedScaleFactor;
    }

    /**
     * Apply computed scale factor to active 3D Model
     */
    function applyScaleToModel() {
        const scale = calculateScale();
        if (scale === null) return;

        // Apply scale factor to 3D mesh smoothly
        Vision2Scale.Viewer.setScaleFactor(scale);

        // Update Before & After Information Cards
        document.getElementById('before-measured').textContent = `${measuredLength.toFixed(1)} units`;
        document.getElementById('before-scale').textContent = `1.00×`;

        document.getElementById('after-calibrated').textContent = `${knownLength.toFixed(1)} m`;
        document.getElementById('after-scale').textContent = `${scale.toFixed(2)}×`;

        // Update Floating Info Panel in 3D Viewer
        document.getElementById('info-scale').textContent = `${scale.toFixed(2)}×`;
        document.getElementById('stat-scale-factor').textContent = `${scale.toFixed(2)}×`;

        // Trigger Pipeline Visual Animation
        Vision2Scale.Pipeline.triggerAnimation();

        Vision2Scale.App.showToast('Successfully applied scale factor to 3D Mesh!', 'success');
    }

    /**
     * Reset Calibration to defaults
     */
    function resetCalibration() {
        document.getElementById('input-known-length').value = 10.0;
        document.getElementById('input-measured-length').value = 5.0;
        knownLength = 10.0;
        measuredLength = 5.0;
        computedScaleFactor = 2.0;

        document.getElementById('display-scale-factor').textContent = '2.00×';
        document.getElementById('before-measured').textContent = '5.0 units';
        document.getElementById('before-scale').textContent = '1.00×';
        document.getElementById('after-calibrated').textContent = '10.0 m';
        document.getElementById('after-scale').textContent = '2.00×';

        Vision2Scale.Viewer.setScaleFactor(1.0);
        document.getElementById('info-scale').textContent = '1.00×';
        document.getElementById('stat-scale-factor').textContent = '1.00×';

        Vision2Scale.App.showToast('Calibration parameters reset to default.', 'info');
    }

    return {
        calculateScale: calculateScale,
        applyScaleToModel: applyScaleToModel,
        resetCalibration: resetCalibration,
        getComputedScale: function () { return computedScaleFactor; }
    };
})();
