declare const __SJIA_LOCAL_TEST_ACCOUNT__: { username: string; password: string; patientId: string } | null;

// 仅在本地验收构建中注入，普通发布构建中的值为 null。
export const localTestAccount = __SJIA_LOCAL_TEST_ACCOUNT__;
