const courses = [
  ["00. Reflective Assessment - Prof. Maya Manishankar", "Semester - 01", 100, "#d39f2d", "dots"],
  ["00. Webinar", "Semester - 01", 0, "#56c6bd", "circles"],
  ["01. Program Information", "Foundation Course", 100, "#8191dd", "diamonds"],
  ["01. Program Information", "Semester - 01", 0, "#9da9eb", "rings"],
  ["02. Course Matrix and Syllabus", "Semester - 01", 0, "#20b48f", "circles"],
  ["03. Certificate in Data Analytics Using Python", "Micro-Credential", null, "#dce5e6", "squares"],
  ["1.1 Mathematical Foundations for Computer Science", "Semester - 01", 4, "#49bfe0", "waves"],
  ["1.2 Computing Concepts and Problem Solving using C", "Semester - 01", 4, "#6b86db", "hex"],
  ["1.3 Operating Systems", "Semester - 01", 10, "#cdd2cc", "diamonds"],
  ["1.4 Data Structures", "Semester - 01", 6, "#24ba96", "squares"],
  ["1.5 Problem Solving using C Lab", "Semester - 01", 66, "#1498d4", "triangles"],
  ["1.6 AI Skills for Future", "Semester - 01", 7, "#df6290", "checks"]
];

const patterns = {
  dots: "radial-gradient(circle at 25% 35%, #6d4d1e 0 15px, transparent 16px), radial-gradient(circle at 64% 55%, #6d4d1e 0 12px, transparent 13px)",
  circles: "radial-gradient(circle at 20% 25%, transparent 0 24px, #195e61 25px 42px, transparent 43px), radial-gradient(circle at 72% 45%, transparent 0 28px, #195e61 29px 50px, transparent 51px)",
  diamonds: "linear-gradient(45deg, #37417d 25%, transparent 25% 75%, #37417d 75%), linear-gradient(45deg, #37417d 25%, transparent 25% 75%, #37417d 75%)",
  rings: "radial-gradient(circle, transparent 0 19px, #4d5f9e 20px 25px, transparent 26px)",
  squares: "linear-gradient(90deg, #386 1px, transparent 1px), linear-gradient(#386 1px, transparent 1px)",
  waves: "repeating-radial-gradient(ellipse at 20% 50%, transparent 0 12px, #207b99 13px 17px, transparent 18px 32px)",
  hex: "radial-gradient(circle at 28% 40%, #203d80 0 26px, transparent 27px), radial-gradient(circle at 75% 62%, #203d80 0 30px, transparent 31px)",
  triangles: "linear-gradient(135deg, #064f87 25%, transparent 25%), linear-gradient(225deg, #064f87 25%, transparent 25%)",
  checks: "linear-gradient(90deg, rgba(86,0,48,.35) 25%, transparent 25% 50%, rgba(86,0,48,.35) 50% 75%, transparent 75%), linear-gradient(rgba(86,0,48,.28) 25%, transparent 25% 50%, rgba(86,0,48,.28) 50% 75%, transparent 75%)"
};

const coursesGrid = document.querySelector("#coursesGrid");
const courseSearch = document.querySelector("#courseSearch");
const profileButton = document.querySelector("#profileButton");
const profileMenu = document.querySelector("#profileMenu");
const viewButton = document.querySelector("#viewButton");

function renderCourses() {
  const query = courseSearch.value.trim().toLowerCase();
  const filtered = courses
    .filter(([title, tag]) => `${title} ${tag}`.toLowerCase().includes(query))
    .sort((a, b) => a[0].localeCompare(b[0]));

  coursesGrid.innerHTML = filtered.map(([title, tag, progress, color, pattern]) => {
    const progressText = progress === null ? "..." : `${progress}% complete`;
    const bar = progress === null ? "" : `<div class="bar"><i style="--value: ${progress}%"></i></div>`;
    return `
      <article class="course-card">
        <div class="cover" style="--cover: ${color}; --pattern: ${patterns[pattern]}">
          <span class="menu-dot">≡</span>
          <span class="tag">${tag}</span>
        </div>
        <div class="course-body"><h3>${title}</h3></div>
        <div class="course-progress">
          <span>${progressText}</span>
          ${bar}
        </div>
      </article>
    `;
  }).join("");
}

document.querySelectorAll(".nav-tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-tab").forEach((tab) => tab.classList.remove("active"));
    document.querySelectorAll(".page").forEach((page) => page.classList.remove("active"));
    button.classList.add("active");
    document.querySelector(`#${button.dataset.page}Page`)?.classList.add("active");
  });
});

profileButton.addEventListener("click", () => {
  const isOpen = profileMenu.classList.toggle("open");
  profileButton.setAttribute("aria-expanded", String(isOpen));
});

document.addEventListener("click", (event) => {
  if (!profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
    profileMenu.classList.remove("open");
    profileButton.setAttribute("aria-expanded", "false");
  }
});

courseSearch.addEventListener("input", renderCourses);

viewButton.addEventListener("click", () => {
  coursesGrid.classList.toggle("list");
  viewButton.textContent = coursesGrid.classList.contains("list") ? "List" : "Card";
});

renderCourses();
