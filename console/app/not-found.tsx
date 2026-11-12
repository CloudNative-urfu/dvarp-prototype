import Link from "next/link";

export default function NotFound() {
  return (
    <div className="empty">
      <strong>Такой страницы нет</strong>
      <span>Возможно, прогон или задача были удалены.</span>
      <Link href="/" className="btn btn-primary">
        На обзор
      </Link>
    </div>
  );
}
