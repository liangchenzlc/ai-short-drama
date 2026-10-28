import type { ImageAngleTransform } from '@host/types/shared/media/image-tools/image-data';
export function buildAngleLabel(params: ImageAngleTransform) {
  const horizontal =
    params.horizontalAngle === 0
      ? '正面视角'
      : params.horizontalAngle > 0
        ? `向右旋转 ${params.horizontalAngle} 度`
        : `向左旋转 ${Math.abs(params.horizontalAngle)} 度`;
  const pitch =
    params.pitchAngle === 0
      ? '水平视角'
      : params.pitchAngle > 0
        ? `俯视 ${params.pitchAngle} 度`
        : `仰视 ${Math.abs(params.pitchAngle)} 度`;
  return `AI 多角度：${horizontal}，${pitch}，镜头距离 ${params.cameraDistance.toFixed(1)}，${params.wideAngle ? '广角' : '标准'}镜头`;
}

export function buildAnglePrompt(params: ImageAngleTransform) {
  return `基于参考图重新生成同一主体的新视角，保持主体、颜色、材质和画面风格一致，不要只做透视变形。${buildAngleLabel(params)}。`;
}
