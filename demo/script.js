const status = document.getElementById("status");
const list = document.getElementById("files");

fetch("data.json")
  .then((response) => {
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  })
  .then((data) => {
    status.textContent = data.message;
    for (const file of data.files) {
      const link = document.createElement("a");
      link.href = file;
      link.textContent = file;
      const item = document.createElement("li");
      item.append(link);
      list.append(item);
    }
  })
  .catch((error) => {
    status.textContent = `Couldn't load data.json: ${error.message}`;
  });
