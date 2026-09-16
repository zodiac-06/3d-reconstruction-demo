/**
 * Vision2Scale - 3D Reconstruction Calibration
 * Animated Calibration Pipeline Workflow Module
 */

window.Vision2Scale = window.Vision2Scale || {};
window.TrackC = window.Vision2Scale;

Vision2Scale.Pipeline = (function () {
    let isAnimating = false;

    /**
     * Trigger step-by-step visual animation through calibration pipeline
     */
    function triggerAnimation() {
        if (isAnimating) return;
        isAnimating = true;

        const steps = document.querySelectorAll('.pipeline-step');
        
        // Reset steps state
        steps.forEach(step => {
            step.classList.remove('active', 'completed');
        });

        let currentStepIndex = 0;

        function animateStep() {
            if (currentStepIndex >= steps.length) {
                isAnimating = false;
                return;
            }

            // Mark previous as completed
            if (currentStepIndex > 0) {
                steps[currentStepIndex - 1].classList.remove('active');
                steps[currentStepIndex - 1].classList.add('completed');
            }

            // Mark current as active
            steps[currentStepIndex].classList.add('active');

            currentStepIndex++;
            setTimeout(animateStep, 450); // 450ms per stage delay
        }

        animateStep();
    }

    return {
        triggerAnimation: triggerAnimation
    };
})();
