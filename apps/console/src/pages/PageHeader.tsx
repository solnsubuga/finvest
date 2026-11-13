interface PageHeaderProps {
  title: string;
  summary: string;
}

export function PageHeader({ title, summary }: PageHeaderProps) {
  return (
    <div>
      <h1 className="m-0 text-3xl font-extrabold tracking-[-0.5px]">{title}</h1>
      <p className="m-0 text-md text-muted">{summary}</p>
    </div>
  );
}
