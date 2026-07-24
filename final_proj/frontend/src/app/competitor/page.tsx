import { redirect } from "next/navigation";

type Query = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function CompetitorRedirect({ searchParams }: { searchParams: Promise<Query> }) {
  const query = await searchParams;
  const area = first(query.areaCode) || first(query.area);
  const industry = first(query.industryCode) || first(query.industry);
  const target = new URLSearchParams({ panel: "competition" });
  if (area) {
    target.set("areaCode", area);
    target.set("area", area);
  }
  if (industry) {
    target.set("industryCode", industry);
    target.set("industry", industry);
  }
  redirect(`/trade?${target.toString()}`);
}
