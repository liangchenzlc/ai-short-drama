import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@ant-design/v5-patch-for-react-19";
import { canvasSignInUrl, establishCanvasSession, isCanvasSignInRequired } from "@/services/host-session";

async function startApplication() {
    try {
        await establishCanvasSession();
        const { bootstrapAppearance } = await import("@/services/appearance-bootstrap");
        await bootstrapAppearance();
        await import("./application");
    } catch (error) {
        if (isCanvasSignInRequired(error)) {
            window.location.replace(canvasSignInUrl());
            return;
        }
        const { renderStartupError } = await import("@/services/host-startup-error");
        renderStartupError();
    }
}

void startApplication();
