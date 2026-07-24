import { redirect } from "next/navigation";

type Query = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function AreaRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ code: string }>;
  searchParams: Promise<Query>;
}) {
  const [{ code }, query] = await Promise.all([params, searchParams]);
  const industry = first(query.industryCode) || first(query.industry);
  const target = new URLSearchParams({ areaCode: code, area: code });
  if (industry) {
    target.set("industryCode", industry);
    target.set("industry", industry);
  }
  if (first(query.panel) === "competition") target.set("panel", "competition");
  redirect(`/trade?${target.toString()}`);
}
