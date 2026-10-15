/**
 * 서버가 준 문자열로 표를 찾을 때 쓴다. 표가 직접 가진 키만 인정하고 `__proto__`·`constructor` 같은
 * 상속 속성은 없는 값(undefined)으로 본다.
 */
export function own<T>(table: Record<string, T>, key: string | null | undefined): T | undefined {
  return key != null && Object.hasOwn(table, key) ? table[key] : undefined;
}
