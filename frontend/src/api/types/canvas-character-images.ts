// 专用读取合同；media_id 与经解析的资源实体分别保留。
export type CanvasCharacterImageSlotDto = {
    reference_id: string;
    role: "primary" | "front" | "side" | "back" | "turnaround_sheet" | "expression_sheet";
    ordinal: number;
    media_id: string;
};

export type CanvasCharacterImageResourceDto = {
    id: string;
    userId: string;
    kind: "image";
    status: "ready";
    provider: "minio";
    endpoint: "";
    bucket: "";
    objectKey: "";
    publicUrl: "";
    mimeType: string;
    size: number;
    width: number;
    height: number;
    durationMs: number;
    etag: string;
    createdAt: string;
    updatedAt: string;
};

export type CanvasCharacterImagesDto = {
    canvas_key: string;
    canvas_row_version: string;
    source_project_id: string;
    asset_id: string;
    version_id: string;
    images: Array<CanvasCharacterImageSlotDto & {
        resource_key: string;
        file_path: string;
        requires_canvas_copy: boolean;
        resource: CanvasCharacterImageResourceDto;
    }>;
};
