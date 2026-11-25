int archive_sum(int left, int right);
int archive_product(int left, int right);

int main(void) {
    return archive_sum(7, 5) != 12 || archive_product(4, 6) != 24;
}
