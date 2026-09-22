package com.zerocode.app.annotation;

/**
 * 业务操作类型
 */
public enum BusinessType {
    /** 其他 */
    OTHER(0, "其他"),
    /** 新增 */
    INSERT(1, "新增"),
    /** 修改 */
    UPDATE(2, "修改"),
    /** 删除 */
    DELETE(3, "删除"),
    /** 授权 */
    GRANT(4, "授权"),
    /** 导出 */
    EXPORT(5, "导出");

    private final int code;
    private final String desc;

    BusinessType(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    public int getCode() {
        return code;
    }

    public String getDesc() {
        return desc;
    }

    public static String getDescByCode(int code) {
        for (BusinessType type : values()) {
            if (type.code == code) {
                return type.desc;
            }
        }
        return "其他";
    }
}
